import subprocess
import os
import sys
import importlib
import time
import random
import asyncio
import logging
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, MessageHandler, filters, ContextTypes

from shivu import (
    collection,
    top_global_groups_collection,
    group_user_totals_collection,
    user_collection,
    user_totals_collection,
    shivuu,
)
from shivu.character_ids import (
    expand_character_id_variants,
    format_character_id,
    normalize_character_document,
    normalize_character_id,
    character_matches_id,
)
from shivu import application, SUPPORT_CHAT, UPDATE_CHAT, db, LOGGER
from shivu.security import is_owner
from shivu.modules import ALL_MODULES
from shivu.modules.leaderboard import update_daily_user_guess, update_daily_group_guess

for module_name in ALL_MODULES:
    importlib.import_module("shivu.modules." + module_name)

import shivu.modules.setrarity as setrarity

from shivu.utils import RARITY_MAP, get_rarity_display

SPAM_REPEAT_THRESHOLD = 10
SPAM_IGNORE_SECONDS = 10 * 60
DEFAULT_MESSAGE_FREQUENCY = 100
MAX_SPAWN_ATTEMPTS = 10
locks: dict[str, asyncio.Lock] = {}
message_counters: dict[str, int] = {}
sent_characters: dict[int, set[int]] = {}
last_characters: dict[int, dict[str, Any]] = {}
first_correct_guesses: dict[int, int] = {}
last_user: dict[str, dict[str, Any]] = {}
warned_users: dict[int, float] = {}

async def _get_chat_lock(chat_id: str) -> asyncio.Lock:
    if chat_id not in locks:
        locks[chat_id] = asyncio.Lock()
    return locks[chat_id]

async def _update_user_info(user_id: int, tg_user: Update.effective_user) -> None:
    try:
        user = await user_collection.find_one({'id': user_id})
        update_fields = {}
        if hasattr(tg_user, 'username') and tg_user.username and (not user or tg_user.username != user.get('username')):
            update_fields['username'] = tg_user.username
        if tg_user.first_name and (not user or tg_user.first_name != user.get('first_name')):
            update_fields['first_name'] = tg_user.first_name
        if user:
            if update_fields:
                await user_collection.update_one({'id': user_id}, {'$set': update_fields})
        else:
            base = {
                'id': user_id,
                'username': getattr(tg_user, 'username', None),
                'first_name': tg_user.first_name,
                'characters': [],
                'balance': 0,
            }
            if update_fields:
                base.update(update_fields)
            await user_collection.insert_one(base)
    except Exception as e:
        LOGGER.exception("Failed to update/insert user info: %s", e)

async def _update_group_user_totals(user_id: int, chat_id: int, tg_user: Update.effective_user) -> None:
    try:
        existing = await group_user_totals_collection.find_one({'user_id': user_id, 'group_id': chat_id})
        update_fields = {}
        if existing:
            if hasattr(tg_user, 'username') and tg_user.username and tg_user.username != existing.get('username'):
                update_fields['username'] = tg_user.username
            if tg_user.first_name and tg_user.first_name != existing.get('first_name'):
                update_fields['first_name'] = tg_user.first_name
            if update_fields:
                await group_user_totals_collection.update_one(
                    {'user_id': user_id, 'group_id': chat_id}, {'$set': update_fields}
                )
            await group_user_totals_collection.update_one(
                {'user_id': user_id, 'group_id': chat_id}, {'$inc': {'count': 1}}
            )
        else:
            await group_user_totals_collection.insert_one({
                'user_id': user_id,
                'group_id': chat_id,
                'username': getattr(tg_user, 'username', None),
                'first_name': tg_user.first_name,
                'count': 1,
            })
    except Exception as e:
        LOGGER.exception("Failed to update group_user_totals: %s", e)

async def _update_top_global_groups(chat_id: int, chat_title: str | None) -> None:
    try:
        group_info = await top_global_groups_collection.find_one({'group_id': chat_id})
        if group_info:
            update_fields = {}
            if chat_title and chat_title != group_info.get('group_name'):
                update_fields['group_name'] = chat_title
            if update_fields:
                await top_global_groups_collection.update_one(
                    {'group_id': chat_id}, {'$set': update_fields}
                )
            await top_global_groups_collection.update_one(
                {'group_id': chat_id}, {'$inc': {'count': 1}}
            )
        else:
            await top_global_groups_collection.insert_one({
                'group_id': chat_id,
                'group_name': chat_title or '',
                'count': 1,
            })
    except Exception as e:
        LOGGER.exception("Failed to update top_global_groups: %s", e)

async def message_counter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return

    chat_id_str = str(update.effective_chat.id)
    user_id = update.effective_user.id
    lock = await _get_chat_lock(chat_id_str)

    async with lock:
        try:
            chat_frequency = await user_totals_collection.find_one({'chat_id': chat_id_str})
            message_frequency = (
                chat_frequency.get('message_frequency', DEFAULT_MESSAGE_FREQUENCY)
                if chat_frequency else DEFAULT_MESSAGE_FREQUENCY
            )
        except Exception:
            message_frequency = DEFAULT_MESSAGE_FREQUENCY
            LOGGER.exception("Error fetching message_frequency; using default")

        last = last_user.get(chat_id_str)
        if last and last.get('user_id') == user_id:
            last['count'] += 1
            if last['count'] >= SPAM_REPEAT_THRESHOLD:
                last_time = warned_users.get(user_id)
                if last_time and (time.time() - last_time) < SPAM_IGNORE_SECONDS:
                    return
                try:
                    await update.message.reply_text(
                        f"ᴅᴏɴ'ᴛ ꜱᴘᴀᴍ, {escape(update.effective_user.first_name)}.\n"
                        f"ʏᴏᴜʀ ᴍᴇꜱꜱᴀɢᴇꜱ ᴡɪʟʟ ʙᴇ ɪɢɴᴏʀᴇᴅ ꜰᴏʀ {SPAM_IGNORE_SECONDS // 60} ᴍɪɴᴜᴛᴇꜱ."
                    )
                except Exception:
                    LOGGER.exception("Failed to send spam warning")
                warned_users[user_id] = time.time()
                return
        else:
            last_user[chat_id_str] = {'user_id': user_id, 'count': 1}

        message_counters.setdefault(chat_id_str, 0)
        message_counters[chat_id_str] += 1

        if message_counters[chat_id_str] >= message_frequency:
            message_counters[chat_id_str] = 0
            await send_image(update, context)

async def send_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    try:
        disabled_rarities = await setrarity.get_disabled_rarities(chat_id)
    except Exception as e:
        LOGGER.exception(f"Failed to get disabled rarities: {e}")
        disabled_rarities = []

    try:
        locked_character_ids = await setrarity.get_locked_character_ids()
    except Exception as e:
        LOGGER.exception(f"Failed to get locked characters: {e}")
        locked_character_ids = []

    try:
        query = {}

        if disabled_rarities:
            disabled_ints = [int(r) for r in disabled_rarities]
            disabled_strs = [str(r) for r in disabled_rarities]
            all_disabled = list(set(disabled_ints + disabled_strs))

            text_rarities = []
            for r in disabled_ints:
                text_rarities.append(RARITY_MAP.get(r, str(r)))

            all_disabled = list(set(all_disabled + text_rarities))
            query['rarity'] = {'$nin': all_disabled}

        if locked_character_ids:
            locked_id_variants = expand_character_id_variants(locked_character_ids)
            if 'rarity' in query:
                query = {
                    '$and': [
                        {'id': {'$nin': locked_id_variants}},
                        query
                    ]
                }
            else:
                query['id'] = {'$nin': locked_id_variants}

        LOGGER.info(f"Query: {query}")
        all_characters = [
            normalize_character_document(character)
            for character in await collection.find(query).to_list(length=None)
        ]
        LOGGER.info(f"Found {len(all_characters)} characters after filtering")

    except Exception:
        LOGGER.exception("Failed to fetch characters from DB")
        all_characters = []

    if not all_characters:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="ɴᴏ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ʀɪɢʜᴛ ɴᴏᴡ. ᴀʟʟ ʀᴀʀɪᴛɪᴇꜱ ᴍᴀʏ ʙᴇ ᴅɪꜱᴀʙʟᴇᴅ ᴏʀ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ʟᴏᴄᴋᴇᴅ."
            )
        except Exception:
            LOGGER.exception("Failed to notify about empty collection")
        return

    sent_characters.setdefault(chat_id, set())

    if len(sent_characters[chat_id]) >= len(all_characters):
        sent_characters[chat_id] = set()

    choices = [
        c for c in all_characters
        if normalize_character_id(c.get('id')) not in sent_characters[chat_id]
    ]
    if not choices:
        choices = all_characters
        sent_characters[chat_id] = set()

    character = random.choice(choices)
    LOGGER.info(f"Selected: ID={character.get('id')}, Rarity={character.get('rarity')}")

    normalized_character_id = normalize_character_id(character.get('id'))
    if normalized_character_id is not None:
        sent_characters[chat_id].add(normalized_character_id)
    last_characters[chat_id] = character
    first_correct_guesses.pop(chat_id, None)

    rarity_display = get_rarity_display(character)
    caption = (
        f"ᴀ ɴᴇᴡ {escape(rarity_display)} ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴘᴘᴇᴀʀᴇᴅ! "
        f"ɢᴜᴇꜱꜱ ᴛʜᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ ᴡɪᴛʜ /guess ᴛᴏ ᴀᴅᴅ ᴛʜᴇᴍ ᴛᴏ ʏᴏᴜʀ ʜᴀʀᴇᴍ."
    )

    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=character.get('img_url'),
            caption=caption,
        )
    except Exception:
        LOGGER.exception("Failed to send photo for character; sending text instead")
        try:
            await context.bot.send_message(chat_id=chat_id, text=caption)
        except Exception:
            LOGGER.exception("Failed to send fallback text message")

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in last_characters:
        return

    if chat_id in first_correct_guesses:
        await update.message.reply_text("ᴀʟʀᴇᴀᴅʏ ɢᴜᴇꜱꜱᴇᴅ ʙʏ ꜱᴏᴍᴇᴏɴᴇ. ᴛʀʏ ɴᴇxᴛ ᴛɪᴍᴇ.")
        return

    guess_text = ' '.join(context.args).strip().lower() if context.args else ''
    if not guess_text:
        await update.message.reply_text("ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ɢᴜᴇꜱꜱ, ᴇ.ɢ. /guess Alice")
        return

    if "()" in guess_text or "&" in guess_text:
        await update.message.reply_text("ʏᴏᴜ ᴄᴀɴ'ᴛ ᴜꜱᴇ ᴛʜᴇꜱᴇ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ɪɴ ʏᴏᴜʀ ɢᴜᴇꜱꜱ.")
        return

    character = last_characters.get(chat_id)
    name_parts = (character.get('name') or '').lower().split()

    if sorted(name_parts) == sorted(guess_text.split()) or any(part == guess_text for part in name_parts):
        first_correct_guesses[chat_id] = user_id

        character_to_store = normalize_character_document(character)
        character_to_store.pop('_id', None)

        try:
            await _update_user_info(user_id, update.effective_user)

            await user_collection.update_one(
                {'id': user_id},
                {'$inc': {'balance': 100}},
                upsert=True
            )
            LOGGER.info(f"Added 100 coins to user {user_id}")
        except Exception as e:
            LOGGER.exception(f"Failed to update user balance: {e}")

        try:
            await user_collection.update_one(
                {'id': user_id},
                {'$push': {'characters': character_to_store}}
            )
        except Exception as e:
            LOGGER.exception(f"Failed updating user character collection: {e}")
            await update.message.reply_text(
                "ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴅᴅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴛᴏ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ."
            )
            return

        try:
            await _update_group_user_totals(user_id, chat_id, update.effective_user)
            await _update_top_global_groups(chat_id, update.effective_chat.title)
        except Exception:
            LOGGER.exception("Failed updating group/global stats")

        try:
            safe_username = update.effective_user.username if update.effective_user.username else ""
            safe_first_name = update.effective_user.first_name if update.effective_user.first_name else "Unknown"

            await update_daily_user_guess(
                user_id=user_id,
                username=safe_username,
                first_name=safe_first_name
            )
        except Exception as e:
            LOGGER.exception(f"Failed to update daily user guess: {e}")

        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                safe_group_name = update.effective_chat.title if update.effective_chat.title else "Unknown Group"
                await update_daily_group_guess(
                    group_id=chat_id,
                    group_name=safe_group_name
                )
            except Exception as e:
                LOGGER.exception(f"Failed to update daily group guess: {e}")

        coin_alert_msg = await update.message.reply_text(
            "ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ! ʏᴏᴜ ɢᴜᴇꜱꜱᴇᴅ ɪᴛ ʀɪɢʜᴛ! "
            "ᴀꜱ ᴀ ʀᴇᴡᴀʀᴅ, 100 ᴄᴏɪɴꜱ ʜᴀᴠᴇ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ.",
            parse_mode='HTML'
        )

        try:
            await coin_alert_msg.set_reaction("🎉")
        except Exception as e:
            LOGGER.exception(f"Failed to set reaction: {e}")

        safe_name = escape(update.effective_user.first_name or "")
        character_name = escape(character.get('name', 'Unknown'))
        anime_name = escape(character.get('anime', 'Unknown'))
        rarity_display = get_rarity_display(character)
        safe_rarity = escape(rarity_display)
        character_id = escape(format_character_id(character.get('id', 'Unknown')))

        reveal_message = (
            f"ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ {safe_name} ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ʜᴀʀᴇᴍ.\n\n"
            f"ɴᴀᴍᴇ: {character_name}\n"
            f"ᴀɴɪᴍᴇ: {anime_name}\n"
            f"ʀᴀʀɪᴛʏ: {safe_rarity}\n"
            f"ɪᴅ: {character_id}\n\n"
            f"ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴀᴅᴅᴇᴅ ᴛᴏ ʜᴀʀᴇᴍ."
        )

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "ꜱᴇᴇ ʜᴀʀᴇᴍ",
                switch_inline_query_current_chat=str(user_id)
            )]]
        )

        try:
            await update.message.reply_text(
                reveal_message,
                reply_markup=keyboard,
                parse_mode='HTML'
            )
        except Exception:
            LOGGER.exception("Failed to send character reveal reply")
            try:
                await update.message.reply_text(
                    f"ʏᴏᴜ ɢᴜᴇꜱꜱᴇᴅ {character.get('name', 'ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ')}"
                )
            except Exception:
                LOGGER.exception("Failed fallback reply")
    else:
        await update.message.reply_text(
            "ᴘʟᴇᴀꜱᴇ ᴡʀɪᴛᴇ ᴛʜᴇ ᴄᴏʀʀᴇᴄᴛ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ."
        )

async def fav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    args = context.args or []
    if not args:
        await update.message.reply_text("ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ: /fav <ɪᴅ>")
        return

    try:
        character_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ.")
        return

    try:
        user = await user_collection.find_one({'id': user_id})
    except Exception:
        LOGGER.exception("Failed to fetch user for fav")
        user = None

    if not user or not user.get('characters'):
        await update.message.reply_text("ʏᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴄᴏʟʟᴇᴄᴛᴇᴅ ᴀɴʏ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ʏᴇᴛ.")
        return

    character = next((c for c in user['characters'] if character_matches_id(c, character_id)), None)
    if not character:
        await update.message.reply_text("ᴛʜᴀᴛ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪꜱ ɴᴏᴛ ɪɴ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ.")
        return

    try:
        await user_collection.update_one({'id': user_id}, {'$addToSet': {'favorites': character_id}})
        await update.message.reply_text(
            f"ᴄʜᴀʀᴀᴄᴛᴇʀ {character.get('name')} ʜᴀꜱ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ꜰᴀᴠᴏʀɪᴛᴇꜱ."
        )
    except Exception:
        LOGGER.exception("Failed to set favorite character")
        await update.message.reply_text("ꜰᴀɪʟᴇᴅ ᴛᴏ ᴍᴀʀᴋ ꜰᴀᴠᴏʀɪᴛᴇ. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.")


async def updatebot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ── Owner-only guard ──────────────────────────────────────────────────────
    if not update.effective_user or not is_owner(update.effective_user.id):
        return

    msg = await update.message.reply_text("🔄 ᴄʜᴇᴄᴋɪɴɢ ꜰᴏʀ ᴜᴘᴅᴀᴛᴇꜱ...")

    try:
        # Pull latest code
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True,
            text=True
        )

        output = result.stdout or result.stderr or "Already up to date."

        if len(output) > 3500:
            output = output[:3500] + "\n\nOutput truncated..."

        await msg.edit_text(f"<pre>{output}</pre>", parse_mode="HTML")

        # If no changes, don't restart
        if "Already up to date." in output:
            await update.message.reply_text("✅ ʙᴏᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴜᴘ ᴛᴏ ᴅᴀᴛᴇ.")
            return

        # Install requirements automatically if file exists
        if os.path.exists("requirements.txt"):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True
            )

        await update.message.reply_text("♻ ʀᴇꜱᴛᴀʀᴛɪɴɢ ʙᴏᴛ...")

        
        os.execv(sys.executable, [sys.executable, "-m", "shivu"])

    except Exception as e:
        await msg.edit_text(f"❌ ᴜᴘᴅᴀᴛᴇ ꜰᴀɪʟᴇᴅ:\n{e}")


def main() -> None:
    setrarity.setup_handlers()

    application.add_handler(CommandHandler(
        ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
    ))
    application.add_handler(CommandHandler("fav", fav, block=False))
    application.add_handler(CommandHandler("true", updatebot))
    application.add_handler(MessageHandler(filters.ALL, message_counter, block=False))

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    shivuu.start()
    LOGGER.info("Senpai Waifu Bot is Back")
    main()
