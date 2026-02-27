import random
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatMemberUpdated
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, ChatMemberHandler
from pymongo.results import UpdateResult

from shivu import application, VIDEO_URL, SUPPORT_CHAT, UPDATE_CHAT, BOT_USERNAME, db, GROUP_ID
from shivu import pm_users as collection
from referral import ensure_referral_schema, process_referral_start, increment_message_count


def small_caps(text: str) -> str:
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
        'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
        'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'S', 'T': 'ᴛ', 'U': 'ᴜ',
        'V': 'ᴠ', 'W': 'ᴡ', 'X': 'X', 'Y': 'ʏ', 'Z': 'ᴢ'
    }
    return ''.join(mapping.get(ch, ch) for ch in text)


def get_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✦ ᴀᴅᴅ ᴍᴇ ʙᴀʙʏ", url=f'http://t.me/{BOT_USERNAME}?startgroup=new')],
        [
            InlineKeyboardButton("✧ sᴜᴘᴘᴏʀᴛ", url=f'https://t.me/{SUPPORT_CHAT}'),
            InlineKeyboardButton("✧ ᴜᴘᴅᴀᴛᴇs", url=f'https://t.me/{UPDATE_CHAT}')
        ],
        [InlineKeyboardButton("✦ ɢᴜɪᴅᴀɴᴄᴇ", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    if user.is_bot:
        return

    user_id = user.id
    first_name = user.first_name
    username = user.username

    try:
        result: UpdateResult = await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "first_name": first_name,
                    "username": username
                },
                "$setOnInsert": {
                    "started_at": update.message.date if update.message else None
                }
            },
            upsert=True
        )

        if result.upserted_id is not None:
            total_users = await collection.count_documents({})
            username_text = f"@{username}" if username else "ɴᴏ ᴜsᴇʀɴᴀᴍᴇ"

            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"#ʙᴏᴛsᴛᴀʀᴛ\n\n"
                     f"ʙᴏᴛ sᴛᴀʀᴛᴇᴅ\n\n"
                     f"ɴᴀᴍᴇ : <a href='tg://user?id={user_id}'>{escape(first_name or 'User')}</a>\n"
                     f"ɪᴅ : <code>{user_id}</code>\n"
                     f"ᴜsᴇʀɴᴀᴍᴇ : {username_text}\n\n"
                     f"ᴛᴏᴛᴀʟ ᴜsᴇʀs : {total_users}",
                parse_mode='HTML'
            )

    except Exception as e:
        print(f"Database error in /start: {e}")

    await ensure_referral_schema(user_id, username, first_name)

    args = context.args
    if args and args[0].startswith("ref_"):
        referral_code = args[0][4:]
        await process_referral_start(user_id, referral_code, context)

    video_url = random.choice(VIDEO_URL)
    keyboard = get_keyboard()

    caption = f"""✨ ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ✨

ɪ'ᴍ ᴀɴ Sᴇɴᴘᴀɪ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴"""

    try:
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=video_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML',
            read_timeout=300,
            write_timeout=300,
            connect_timeout=60
        )
    except Exception as e:
        print(f"Video send failed: {e}")
        try:
            await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=video_url,
                caption=caption,
                reply_markup=keyboard,
                parse_mode='HTML',
                read_timeout=60,
                write_timeout=60
            )
        except Exception as e2:
            print(f"Animation send failed: {e2}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                reply_markup=keyboard,
                parse_mode='HTML'
            )


async def track_group_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    new_status = result.new_chat_member
    old_status = result.old_chat_member

    if new_status.user.id != context.bot.id:
        return

    if old_status.status in ["left", "kicked"] and new_status.status in ["member", "administrator"]:
        try:
            added_by = result.from_user
            added_by_name = added_by.first_name or "Unknown"
            added_by_link = f"<a href='tg://user?id={added_by.id}'>{escape(added_by_name)}</a>"

            try:
                chat_info = await context.bot.get_chat(chat.id)
                invite_link = chat_info.invite_link
                if not invite_link:
                    try:
                        invite_link = await context.bot.create_chat_invite_link(chat.id)
                        invite_link = invite_link.invite_link
                    except:
                        invite_link = None
            except:
                invite_link = None

            group_link_text = invite_link if invite_link else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"

            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"#ᴀᴅᴅɢʀᴏᴜᴘ\n\n"
                     f"ɢʀᴏᴜᴘ ɴᴀᴍᴇ : {escape(chat.title or 'Unknown')}\n"
                     f"ɢʀᴏᴜᴘ ɪᴅ : <code>{chat.id}</code>\n"
                     f"ɢʀᴏᴜᴘ ᴛʏᴘᴇ : {small_caps(chat.type)}\n"
                     f"ɢʀᴏᴜᴘ ʟɪɴᴋ : {group_link_text}\n"
                     f"ᴀᴅᴅᴇᴅ ʙʏ : {added_by_link}",
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            print(f"Error tracking group add: {e}")

    elif old_status.status in ["member", "administrator"] and new_status.status in ["left", "kicked"]:
        try:
            removed_by = result.from_user
            removed_by_name = removed_by.first_name or "Unknown"
            removed_by_link = f"<a href='tg://user?id={removed_by.id}'>{escape(removed_by_name)}</a>"

            try:
                chat_info = await context.bot.get_chat(chat.id)
                invite_link = chat_info.invite_link
            except:
                invite_link = None

            group_link_text = invite_link if invite_link else "ᴘʀɪᴠᴀᴛᴇ ɢʀᴏᴜᴘ"

            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"#ʟᴇғᴛ\n\n"
                     f"ɢʀᴏᴜᴘ ɴᴀᴍᴇ : {escape(chat.title or 'Unknown')}\n"
                     f"ɢʀᴏᴜᴘ ɪᴅ : <code>{chat.id}</code>\n"
                     f"ɢʀᴏᴜᴘ ᴛʏᴘᴇ : {small_caps(chat.type)}\n"
                     f"ɢʀᴏᴜᴘ ʟɪɴᴋ : {group_link_text}\n"
                     f"ʀᴇᴍᴏᴠᴇᴅ ʙʏ : {removed_by_link}",
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            print(f"Error tracking group remove: {e}")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'help':
        help_text = f"""✦ {small_caps('guidance from senpai')} ✦

✦ ── 『 ʜᴀʀᴇᴍ ᴄᴏᴍᴍᴀɴᴅ ʟɪsᴛ 』 ── ✦

/guess  
↳ ɢᴜᴇss ᴛʜᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ  

/bal  
↳ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ʙᴀʟᴀɴᴄᴇ  

/fav  
↳ ᴀᴅᴅ ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴛᴏ ғᴀᴠᴏʀɪᴛᴇs  

/collection  
↳ ᴠɪᴇᴡ ʏᴏᴜʀ ʜᴀʀᴇᴍ ᴄᴏʟʟᴇᴄᴛɪᴏɴ  

/leaderboard  
↳ ᴄʜᴇᴄᴋ ᴛʜᴇ ᴛᴏᴘ ᴜsᴇʀ ʟɪsᴛ  

/gift  
↳ ɢɪғᴛ ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴛᴏ ᴀɴᴏᴛʜᴇʀ ᴜsᴇʀ  

/trade  
↳ ᴛʀᴀᴅᴇ ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴡɪᴛʜ ᴀɴᴏᴛʜᴇʀ ᴜsᴇʀ  

/shop  
↳ ᴏᴘᴇɴ ᴛʜᴇ sʜᴏᴘ  

/smode  
↳ ᴄʜᴀɴɢᴇ ʜᴀʀᴇᴍ ᴍᴏᴅᴇ  

/s  
↳ ᴠɪᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ ғʀᴏᴍ ᴡᴀɪғᴜ ɪᴅ  

/find  
↳ ғɪɴᴅ ʜᴏᴡ ᴍᴀɴʏ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴇxɪsᴛ ᴡɪᴛʜ ᴀ ɴᴀᴍᴇ  

/redeem  
↳ ʀᴇᴅᴇᴇᴍ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴀɴᴅ ᴄᴏɪɴs  

/sclaim  
↳ ᴄʟᴀɪᴍ ʏᴏᴜʀ ᴅᴀɪʟʏ ᴡᴀɪғᴜ  

/claim  
↳ ᴄʟᴀɪᴍ ʏᴏᴜʀ ᴅᴀɪʟʏ ᴄᴏᴜɴᴛ  

/pay  
↳ sᴇɴᴅ ᴄᴏɪɴs ᴛᴏ ᴀɴᴏᴛʜᴇʀ ᴜsᴇʀ  

/referral  
↳ ᴠɪᴇᴡ ʀᴇғᴇʀʀᴀʟ ᴅᴀsʜʙᴏᴀʀᴅ  

✦ ───────────────── ✦"""

        help_keyboard = [[InlineKeyboardButton("✧ ʀᴇᴛᴜʀɴ", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(help_keyboard)

        await query.edit_message_caption(
            caption=help_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    elif query.data == 'back':
        caption = f"""✨ ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ✨

ɪ'ᴍ ᴀɴ Sᴇɴᴘᴀɪ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴"""

        keyboard = get_keyboard()
        await query.edit_message_caption(
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )


application.add_handler(CallbackQueryHandler(button, pattern='^help$|^back$'))
application.add_handler(ChatMemberHandler(track_group_status, ChatMemberHandler.MY_CHAT_MEMBER))
start_handler = CommandHandler('start', start)
application.add_handler(start_handler)
