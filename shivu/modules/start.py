import random
from html import escape 
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from pymongo.results import UpdateResult

from shivu import application, PHOTO_URL, SUPPORT_CHAT, UPDATE_CHAT, BOT_USERNAME, db, GROUP_ID
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
'V': 'ᴠ', 'W': 'ᴡ', 'X': 'X', 'Y': 'ʏ', 'Z': 'ᴢ',
'0': '𝟶', '1': '𝟷', '2': '𝟸', '3': '𝟹', '4': '𝟺', '5': '𝟻', 
'6': '𝟼', '7': '𝟽', '8': '𝟾', '9': '𝟿'
}
return ''.join(mapping.get(ch, ch) for ch in text)

def get_keyboard() -> InlineKeyboardMarkup:
keyboard = [
[InlineKeyboardButton("✦ ᴀᴅᴅ ᴍᴇ ʙᴀʙʏ ✦", url=f'http://t.me/{BOT_USERNAME}?startgroup=new')],
[
InlineKeyboardButton("✦ sᴜᴘᴘᴏʀᴛ", url=f'https://t.me/{SUPPORT_CHAT}'),
InlineKeyboardButton("✦ ᴜᴘᴅᴀᴛᴇs", url=f'https://t.me/{UPDATE_CHAT}')
],
[InlineKeyboardButton("✦ ʜᴇʟᴘ", callback_data='help')]
]
return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
user = update.effective_user
user_id = user.id
first_name = user.first_name
username = user.username

<b>✦ {small_caps('welcome to senpai waifu bot')} ✦</b>

<i>ᴀɴ ᴇʟɪᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs</i>
"""

<b>✦ {small_caps('senpai waifu bot')} ɪs ᴀʟɪᴠᴇ</b>

<i>ᴄᴏɴɴᴇᴄᴛ ᴡɪᴛʜ ᴍᴇ ɪɴ ᴘʀɪᴠᴀᴛᴇ ғᴏʀ ᴇxᴄʟᴜsɪᴠᴇ ғᴇᴀᴛᴜʀᴇs</i>
"""

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
query = update.callback_query
await query.answer()

<b>✦ {small_caps('senpai waifu bot help guide')} ✦</b>

<b>✦ ɢᴀᴍᴇ ᴄᴏᴍᴍᴀɴᴅs</b>
<code>/guess</code> - ᴄᴀᴛᴄʜ ᴀ sᴘᴀᴡɴᴇᴅ ᴄʜᴀʀᴀᴄᴛᴇʀ (ɢʀᴏᴜᴘ ᴏɴʟʏ)
<code>/harem</code> - ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ
<code>/fav</code> - ᴀᴅᴅ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴛᴏ ғᴀᴠᴏʀɪᴛᴇs
<code>/trade</code> - ᴛʀᴀᴅᴇ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴡɪᴛʜ ᴏᴛʜᴇʀs

<b>✦ ᴜᴛɪʟɪᴛʏ ᴄᴏᴍᴍᴀɴᴅs</b>
<code>/gift</code> - ɢɪғᴛ ᴄʜᴀʀᴀᴄᴛᴇʀs ᴛᴏ ᴜsᴇʀs (ɢʀᴏᴜᴘs)
<code>/changetime</code> - ᴄʜᴀɴɢᴇ sᴘᴀᴡɴ ᴛɪᴍᴇ (ɢʀᴏᴜᴘ ᴀᴅᴍɪɴs)

<b>✦ sᴛᴀᴛɪsᴛɪᴄs ᴄᴏᴍᴍᴀɴᴅs</b>
<code>/top</code> - ᴛᴏᴘ ᴜsᴇʀs ɢʟᴏʙᴀʟʟʏ
<code>/ctop</code> - ᴛᴏᴘ ᴜsᴇʀs ɪɴ ᴛʜɪs ᴄʜᴀᴛ
<code>/topgroups</code> - ᴛᴏᴘ ᴀᴄᴛɪᴠᴇ ɢʀᴏᴜᴘs
"""

<b>✦ {small_caps('welcome to senpai waifu bot')} ✦</b>

<i>ᴀɴ ᴇʟɪᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜᴇʀ ʙᴏᴛ ᴅᴇsɪɢɴᴇᴅ ғᴏʀ ᴜʟᴛɪᴍᴀᴛᴇ ᴄᴏʟʟᴇᴄᴛᴏʀs</i>
"""

application.add_handler(CallbackQueryHandler(button, pattern='^help$|^back$'))
start_handler = CommandHandler('start', start)
application.add_handler(start_handler)