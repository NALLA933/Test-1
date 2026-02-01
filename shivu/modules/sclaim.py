import random
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
from html import escape
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler

from shivu import application, user_collection, collection, db, LOGGER

claim_codes_collection = db.claim_codes

ALLOWED_GROUP_ID = -1003100468240
SUPPORT_GROUP = "https://t.me/THE_DRAGON_SUPPORT"
SUPPORT_CHANNEL = "https://t.me/Senpai_Updates"
SUPPORT_GROUP_ID = -1003100468240
SUPPORT_CHANNEL_ID = -1003002819368

ENABLE_MEMBERSHIP_CHECK = True

ALLOWED_RARITIES = [2, 3, 4]

RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ",
    2: "🔵 ʀᴀʀᴇ",
    3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ",
    6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ",
    8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
    14: "🍭 ᴋᴀᴡᴀɪɪ",
    15: "🧬 ʜʏʙʀɪᴅ"
}

SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
    ' ': ' ', ':': ':', '!': '!', '?': '?', '.': '.', ',': ',', '-': '-',
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5',
    '6': '6', '7': '7', '8': '8', '9': '9'
}

_active_claims = {}
_claim_locks = {}


def _get_lock(user_id: int, command_type: str):
    key = f"{user_id}_{command_type}"
    if key not in _claim_locks:
        _claim_locks[key] = asyncio.Lock()
    return _claim_locks[key]


def _normalize_datetime(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _utcnow():
    return datetime.now(timezone.utc)


def to_small_caps(text: str) -> str:
    return ''.join(SMALL_CAPS_MAP.get(char, char) for char in str(text))


def get_rarity_display(rarity: int) -> str:
    return RARITY_MAP.get(rarity, f"⚪ ᴜɴᴋɴᴏᴡɴ ({rarity})")


def generate_coin_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace('0', '').replace('O', '').replace('I', '').replace('L', '').replace('1', '')
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    return f"COIN-{random_part}"


async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id

    try:
        try:
            group_member = await context.bot.get_chat_member(SUPPORT_GROUP_ID, user_id)
            if group_member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            LOGGER.warning(f"Cannot check support group membership (bot needs admin rights): {e}")

        try:
            channel_member = await context.bot.get_chat_member(SUPPORT_CHANNEL_ID, user_id)
            if channel_member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            LOGGER.warning(f"Cannot check update channel membership (bot needs admin rights): {e}")
            pass

        return True
    except Exception as e:
        LOGGER.error(f"Error checking membership: {e}")
        return True


async def show_join_buttons(update: Update):
    keyboard = [
        [InlineKeyboardButton("📢 Update Channel", url=SUPPORT_CHANNEL)],
        [InlineKeyboardButton("👥 Support Group", url=SUPPORT_GROUP)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"<b>⚠️ {to_small_caps('JOIN REQUIRED')}</b>\n\n"
        f"🔒 {to_small_caps('You need to join our Update Channel and Support Group first!')}\n\n"
        f"📌 {to_small_caps('Please join both and try again:')}",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


async def check_cooldown(user_id: int, command_type: str) -> bool:
    user = await user_collection.find_one(
        {"id": user_id},
        {f"last_{command_type}": 1}
    )

    if not user:
        return True

    last_claim_time = user.get(f"last_{command_type}", None)

    if last_claim_time is None:
        return True

    last_claim_time = _normalize_datetime(last_claim_time)
    if last_claim_time is None:
        return True

    time_diff = _utcnow() - last_claim_time
    if time_diff >= timedelta(hours=24):
        return True

    return False


async def get_cooldown_time(user_id: int, command_type: str) -> Optional[str]:
    user = await user_collection.find_one(
        {"id": user_id},
        {f"last_{command_type}": 1}
    )

    if not user or not user.get(f"last_{command_type}"):
        return None

    last_claim_time = _normalize_datetime(user[f"last_{command_type}"])
    if last_claim_time is None:
        return None

    next_claim_time = last_claim_time + timedelta(hours=24)
    remaining = next_claim_time - _utcnow()

    if remaining.total_seconds() <= 0:
        return None

    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)

    return f"{hours}h {minutes}m"


async def sclaim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id != ALLOWED_GROUP_ID:
        await show_join_buttons(update)
        return

    if ENABLE_MEMBERSHIP_CHECK:
        is_member = await check_membership(update, context)
        if not is_member:
            await show_join_buttons(update)
            return

    lock = _get_lock(user_id, "sclaim")
    async with lock:
        can_claim = await check_cooldown(user_id, "sclaim")
        if not can_claim:
            remaining_time = await get_cooldown_time(user_id, "sclaim")
            await update.message.reply_text(
                f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                f"⏳ {to_small_caps(f'You can use /sclaim again in:')} <b>{remaining_time}</b>\n\n"
                f"💡 {to_small_caps('Come back later!')}",
                parse_mode="HTML"
            )
            return

        pipeline = [
            {"$match": {"rarity": {"$in": ALLOWED_RARITIES}}},
            {"$sample": {"size": 1}}
        ]

        characters = await collection.aggregate(pipeline).to_list(1)

        if not characters:
            await update.message.reply_text(
                f"❌ {to_small_caps('No characters available at the moment!')}"
            )
            return

        character = characters[0]
        character_id = character.get("id")
        character_name = character.get("name", "Unknown")
        anime_name = character.get("anime", "Unknown")
        rarity = character.get("rarity", 1)
        img_url = character.get("img_url", "")

        now = _utcnow()
        result = await user_collection.update_one(
            {
                "id": user_id,
                "$or": [
                    {f"last_sclaim": {"$exists": False}},
                    {f"last_sclaim": {"$lte": now - timedelta(hours=24)}}
                ]
            },
            {
                "$push": {
                    "characters": {
                        "id": character_id,
                        "name": character_name,
                        "anime": anime_name,
                        "rarity": rarity,
                        "img_url": img_url
                    }
                },
                "$set": {"last_sclaim": now}
            },
            upsert=True
        )

        if result.matched_count == 0 and result.upserted_id is None:
            remaining_time = await get_cooldown_time(user_id, "sclaim")
            await update.message.reply_text(
                f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                f"⏳ {to_small_caps(f'You can use /sclaim again in:')} <b>{remaining_time}</b>\n\n"
                f"💡 {to_small_caps('Come back later!')}",
                parse_mode="HTML"
            )
            return

        rarity_display = get_rarity_display(rarity)

        message = (
            f"<b>🎉 {to_small_caps('CONGRATULATIONS!')}</b>\n\n"
            f"🎴 <b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
            f"📺 <b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
            f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}\n"
            f"🆔 <b>{to_small_caps('ID:')}</b> {character_id}\n\n"
            f"✅ {to_small_caps('Character has been added to your collection!')}"
        )

        if img_url:
            try:
                await update.message.reply_photo(
                    photo=img_url,
                    caption=message,
                    parse_mode="HTML"
                )
            except Exception as e:
                LOGGER.error(f"Failed to send image: {e}")
                await update.message.reply_text(message, parse_mode="HTML")
        else:
            await update.message.reply_text(message, parse_mode="HTML")

        LOGGER.info(f"User {user_id} claimed character {character_id} ({character_name}) via /sclaim")


async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id != ALLOWED_GROUP_ID:
        await show_join_buttons(update)
        return

    if ENABLE_MEMBERSHIP_CHECK:
        is_member = await check_membership(update, context)
        if not is_member:
            await show_join_buttons(update)
            return

    lock = _get_lock(user_id, "claim")
    async with lock:
        can_claim = await check_cooldown(user_id, "claim")
        if not can_claim:
            remaining_time = await get_cooldown_time(user_id, "claim")
            await update.message.reply_text(
                f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                f"⏳ {to_small_caps(f'You can use /claim again in:')} <b>{remaining_time}</b>\n\n"
                f"💡 {to_small_caps('Come back later!')}",
                parse_mode="HTML"
            )
            return

        coin_amount = random.randint(1000, 3000)
        coin_code = generate_coin_code()

        max_attempts = 10
        for _ in range(max_attempts):
            if not await claim_codes_collection.find_one({"code": coin_code}):
                break
            coin_code = generate_coin_code()

        now = _utcnow()

        try:
            await claim_codes_collection.insert_one({
                "code": coin_code,
                "user_id": user_id,
                "amount": coin_amount,
                "created_at": now,
                "is_redeemed": False
            })
        except Exception as e:
            LOGGER.error(f"Failed to insert coin code: {e}")
            await update.message.reply_text(
                f"❌ {to_small_caps('Failed to generate code. Please try again.')}"
            )
            return

        await user_collection.update_one(
            {"id": user_id},
            {"$set": {"last_claim": now}},
            upsert=True
        )

        await update.message.reply_text(
            f"<b>💰 {to_small_caps('COIN CODE GENERATED!')}</b>\n\n"
            f"🎟️ <b>{to_small_caps('Your Code:')}</b> <code>{coin_code}</code>\n"
            f"💎 <b>{to_small_caps('Amount:')}</b> {coin_amount:,} {to_small_caps('coins')}\n\n"
            f"📌 {to_small_caps('Use')} <code>/credeem {coin_code}</code> {to_small_caps('to claim your coins!')}\n"
            f"⏰ {to_small_caps('Valid for 24 hours')}",
            parse_mode="HTML"
        )

        LOGGER.info(f"User {user_id} generated coin code {coin_code} for {coin_amount} coins")


async def credeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if len(context.args) < 1:
        usage_msg = (
            f"<b>🎁 {to_small_caps('REDEEM CODE')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/credeem &lt;CODE&gt;</code>\n\n"
            f"💡 {to_small_caps('Redeem your coin codes to add coins to your balance!')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return

    code = context.args[0].upper()

    lock = _get_lock(user_id, f"redeem_{code}")
    async with lock:
        code_doc = await claim_codes_collection.find_one({
            "code": code,
            "user_id": user_id
        })

        if not code_doc:
            await update.message.reply_text(
                f"<b>❌ {to_small_caps('INVALID CODE')}</b>\n\n"
                f"⚠️ {to_small_caps('This code does not exist or does not belong to you.')}\n\n"
                f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                parse_mode="HTML"
            )
            return

        if code_doc.get("is_redeemed", False):
            await update.message.reply_text(
                f"<b>❌ {to_small_caps('CODE ALREADY REDEEMED')}</b>\n\n"
                f"⚠️ {to_small_caps('This code has already been used.')}\n\n"
                f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                parse_mode="HTML"
            )
            return

        created_at = _normalize_datetime(code_doc.get("created_at"))
        if created_at:
            time_diff = _utcnow() - created_at
            if time_diff > timedelta(hours=24):
                await update.message.reply_text(
                    f"<b>❌ {to_small_caps('CODE EXPIRED')}</b>\n\n"
                    f"⚠️ {to_small_caps('This code has expired (24 hours limit).')}\n\n"
                    f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                    parse_mode="HTML"
                )
                return

        coin_amount = code_doc.get("amount", 0)
        now = _utcnow()

        redeem_result = await claim_codes_collection.update_one(
            {
                "code": code,
                "user_id": user_id,
                "is_redeemed": False
            },
            {"$set": {"is_redeemed": True, "redeemed_at": now}}
        )

        if redeem_result.matched_count == 0:
            await update.message.reply_text(
                f"<b>❌ {to_small_caps('CODE ALREADY REDEEMED')}</b>\n\n"
                f"⚠️ {to_small_caps('This code has already been used.')}\n\n"
                f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                parse_mode="HTML"
            )
            return

        user_result = await user_collection.find_one_and_update(
            {"id": user_id},
            {
                "$inc": {"balance": coin_amount},
                "$set": {"last_credeem": now}
            },
            upsert=True,
            return_document=True
        )

        new_balance = user_result.get("balance", 0) if user_result else coin_amount

        await update.message.reply_text(
            f"<b>✅ {to_small_caps('CODE REDEEMED SUCCESSFULLY!')}</b>\n\n"
            f"💰 <b>{to_small_caps('Coins Added:')}</b> {coin_amount:,}\n"
            f"💎 <b>{to_small_caps('New Balance:')}</b> {new_balance:,} {to_small_caps('coins')}\n\n"
            f"🎉 {to_small_caps('Enjoy your coins!')}",
            parse_mode="HTML"
        )

        LOGGER.info(f"User {user_id} redeemed code {code} for {coin_amount} coins")


def register_handlers():
    application.add_handler(CommandHandler("sclaim", sclaim_command, block=False))
    application.add_handler(CommandHandler("claim", claim_command, block=False))
    application.add_handler(CommandHandler("credeem", credeem_command, block=False))
    LOGGER.info("Claim system handlers registered successfully")


register_handlers()