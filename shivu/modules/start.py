import random
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from pymongo.results import UpdateResult

from shivu import application, VIDEO_URL, SUPPORT_CHAT, UPDATE_CHAT, BOT_USERNAME, db, GROUP_ID
from shivu import pm_users as collection


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
    user = update.effective_user
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
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=f"✦ ɴᴇᴡ ᴘʀᴇsᴇɴᴄᴇ ᴅᴇᴛᴇᴄᴛᴇᴅ\n"
                     f"─────────────────\n"
                     f"{escape(first_name or 'User')}\n"
                     f"ɪᴅ · {user_id}",
                parse_mode='HTML'
            )
    
    except Exception as e:
        print(f"Database error in /start: {e}")
    
    video_url = random.choice(VIDEO_URL)
    keyboard = get_keyboard()
    
    if update.effective_chat.type == "private":
        caption = f"""✨ ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ✨

ɪ'ᴍ ᴀɴ Sᴇɴᴘᴀɪ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴"""

        await context.bot.send_video(
    chat_id=update.effective_chat.id,
    video=video_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    
    else:
        caption = f"""✨ ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ Sᴇɴᴘᴀɪ Wᴀɪғᴜ Bᴏᴛ ✨

ɪ'ᴍ ᴀɴ Sᴇɴᴘᴀɪ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs! 🎴"""

        await context.bot.send_video(
    chat_id=update.effective_chat.id,
    video=video_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode='HTML'
        )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    if query.data == 'help':
        help_text = f"""✦ {small_caps('guidance from senpai')} ✦

─────────────────

ɪ ᴅᴏ ɴᴏᴛ ᴇxᴘʟᴀɪɴ.
ɪ ᴇᴍʙᴏᴅʏ.

ʏᴏᴜ ᴡɪʟʟ ᴜɴᴅᴇʀsᴛᴀɴᴅ ᴍʏ ᴘᴜʀᴘᴏsᴇ
ᴛʜʀᴏᴜɢʜ ᴘʀᴇsᴇɴᴄᴇ ᴀʟᴏɴᴇ.

✧ ᴡʜᴀᴛ ᴛᴏ ᴋɴᴏᴡ ✧

• ɪ ᴀᴍ ɴᴏᴛ ғᴏʀ ᴇᴠᴇʀʏᴏɴᴇ
• ᴏɴʟʏ ᴛʜᴇ ʀᴇғɪɴᴇᴅ ᴡɪʟʟ ᴀᴘᴘʀᴇᴄɪᴀᴛᴇ
• ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
• ᴡɪᴛɴᴇss ᴇʟᴇɢᴀɴᴄᴇ

─────────────────

ǫᴜᴇsᴛɪᴏɴs ᴀʀᴇ ʙᴇɴᴇᴀᴛʜ ᴜs.
ᴇxᴘᴇʀɪᴇɴᴄᴇ ɪs ᴀʟʟ."""

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
start_handler = CommandHandler('start', start)
application.add_handler(start_handler)