# -*- coding: utf-8 -*-
import os
import random
import asyncio
import aiohttp

from typing import Optional

# Telegram / Pyrogram / PTB
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from pyrogram import filters
from pyrogram.types import Message
from telegraph import upload_file  # required for telegraph fallback

# Database helpers
from pymongo import ReturnDocument, UpdateOne

# Import shared objects and config values from shivu package (uses __init__.py + config)
from shivu import (
    shivuu,               # pyrogram Client
    application,          # PTB Application
    collection,           # main character collection (motor)
    user_collection,      # user collection (motor)
    user_totals_collection,
    top_global_groups_collection,
    db,
    CHARA_CHANNEL_ID,     # channel id from config
    OWNER_ID,
    SUDO_USERS,
    SUPPORT_CHAT,
    UPDATE_CHAT,
)

# Optional: read API keys from env, with the old value as default if needed
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "6d52008ec9026912f9f50c8ca96a09c3")

# Rarity map and wrong format text (kept as before)
WRONG_FORMAT_TEXT = """Wrong ❌ format...  eg. /upload reply to photo muzan-kibutsuji Demon-slayer 3

format:- /upload reply character-name anime-name rarity-number

use rarity number accordingly rarity Map

RARITY_MAP = {
    1: (1, "⚪ ᴄᴏᴍᴍᴏɴ"),
    2: (2, "🔵 ʀᴀʀᴇ"),
    3: (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: (4, "💮 ꜱᴘᴇᴄɪᴀʟ"),
    5: (5, "👹 ᴀɴᴄɪᴇɴᴛ"),
    6: (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ"),
    7: (7, "🔮 ᴇᴘɪᴄ"),
    8: (8, "🪐 ᴄᴏꜱᴍɪᴄ"),
    9: (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ"),
    10: (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ"),
    11: (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ"),
    12: (12, "🌸 ꜱᴘʀɪɴɢ"),
    13: (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ"),
    14: (14, "🍭 ᴋᴀᴡᴀɪɪ"),
    15: (15, "🧬 ʜʏʙʀɪᴅ"),
}
"""

RARITY_MAP = {
    1: (1, "⚪ ᴄᴏᴍᴍᴏɴ"),
    2: (2, "🔵 ʀᴀʀᴇ"),
    3: (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: (4, "💮 ꜱᴘᴇᴄɪᴀʟ"),
    5: (5, "👹 ᴀɴᴄɪᴇɴᴛ"),
    6: (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ"),
    7: (7, "🔮 ᴇᴘɪᴄ"),
    8: (8, "🪐 ᴄᴏꜱᴍɪᴄ"),
    9: (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ"),
    10: (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ"),
    11: (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ"),
    12: (12, "🌸 ꜱᴘʀɪɴɢ"),
    13: (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ"),
    14: (14, "🍭 ᴋᴀᴡᴀɪɪ"),
    15: (15, "🧬 ʜʏʙʀɪᴅ"),
}

# Concurrency helpers for ID assignment
active_ids = set()
id_lock = asyncio.Lock()

# --- Filters: use SUDO_USERS from config for sudo / uploader permissions ---
def sudo_filter_func(_, __, message: Message):
    if not message.from_user:
        return False
    return message.from_user.id in ([OWNER_ID] + [u for u in SUDO_USERS if u != OWNER_ID])

def uploader_filter_func(_, __, message: Message):
    # uploader is same as sudo users for now
    if not message.from_user:
        return False
    return message.from_user.id in ([OWNER_ID] + [u for u in SUDO_USERS if u != OWNER_ID])

sudo_filter = filters.create(sudo_filter_func)
uploader_filter = filters.create(uploader_filter_func)

# --- Upload helpers (imgBB primary, Telegraph/Catbox fallback) ---
async def upload_to_imgbb(file_path: str, api_key: Optional[str] = IMGBB_API_KEY) -> str:
    url = "https://api.imgbb.com/1/upload"
    with open(file_path, "rb") as f:
        file_data = f.read()

    data = aiohttp.FormData()
    data.add_field("key", api_key)
    data.add_field("image", file_data, filename=os.path.basename(file_path))

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            result = await response.json()
            if response.status == 200 and result.get("success"):
                return result["data"]["url"]
            else:
                error_msg = result.get("error", {}).get("message", "Unknown error")
                raise Exception(f"ImgBB upload failed: {error_msg}")

async def upload_to_telegraph(file_path: str) -> str:
    try:
        result = upload_file(file_path)  # telegraph.upload_file (synchronous)
        if isinstance(result, list) and len(result) > 0:
            return f"https://telegra.ph{result[0]}"
        raise Exception("Telegraph upload returned no result")
    except Exception as e:
        raise Exception(f"Telegraph upload error: {e}")

async def upload_to_catbox(file_path: str) -> str:
    url = "https://catbox.moe/user/api.php"
    with open(file_path, "rb") as f:
        file_data = f.read()

    data = aiohttp.FormData()
    data.add_field("reqtype", "fileupload")
    data.add_field("fileToUpload", file_data, filename=os.path.basename(file_path))

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status == 200:
                return (await response.text()).strip()
            raise Exception(f"Catbox upload failed with status {response.status}")

async def upload_image_with_fallback(file_path: str) -> str:
    services = [upload_to_imgbb, upload_to_telegraph, upload_to_catbox]
    last_error = None
    for svc in services:
        try:
            url = await svc(file_path)
            return url
        except Exception as e:
            last_error = e
            continue
    raise Exception(f"All upload services failed. Last error: {last_error}")

def check_file_size(file_path: str, max_size_mb: int = 30) -> bool:
    file_size = os.path.getsize(file_path)
    if file_size > max_size_mb * 1024 * 1024:
        raise Exception(f"File size ({file_size/1024/1024:.2f} MB) exceeds {max_size_mb} MB limit.")
    return True

# --- ID allocation helpers ---
async def find_available_id() -> str:
    async with id_lock:
        cursor = collection.find().sort("id", 1)
        docs = await cursor.to_list(length=None)
        ids = [doc["id"] for doc in docs]

        if not ids:
            candidate_id = "01"
            active_ids.add(candidate_id)
            return candidate_id

        int_ids = [int(i) for i in ids]
        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                active_ids.add(candidate_id)
                return candidate_id
        candidate_id = str(max(int_ids) + 1).zfill(2)
        active_ids.add(candidate_id)
        return candidate_id

async def find_available_ids() -> str:
    async with id_lock:
        cursor = collection.find().sort("id", 1)
        docs = await cursor.to_list(length=None)
        ids = [doc["id"] for doc in docs]

        if not ids:
            return "01"

        int_ids = [int(i) for i in ids]
        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                return candidate_id
        return str(max(int_ids) + 1).zfill(2)

# --- Commands and handlers (use shivuu for Pyrogram handlers) ---
@shivuu.on_message(filters.command(["uid"]) & uploader_filter)
async def ulo(client: shivuu.__class__, message: Message):
    available_id = await find_available_ids()
    await client.send_message(chat_id=message.chat.id, text=f"{available_id}")

@shivuu.on_message(filters.command(["upload"]) & uploader_filter)
async def ul(client: shivuu.__class__, message: Message):
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document.")
        return

    args = message.text.split()
    if len(args) != 4:
        await client.send_message(chat_id=message.chat.id, text=WRONG_FORMAT_TEXT)
        return

    character_name = args[1].replace("-", " ").title()
    anime = args[2].replace("-", " ").title()
    try:
        rarity = int(args[3])
    except ValueError:
        await message.reply_text("Rarity must be a number.")
        return

    if rarity not in RARITY_MAP:
        await message.reply_text("Invalid rarity value. Use a number between 1 and 15.")
        return

    rarity_text = RARITY_MAP[rarity][1]
    available_id = None
    path = None

    try:
        available_id = await find_available_id()
        processing_message = await message.reply("<ᴘʀᴏᴄᴇꜱꜱɪɴɢ>....")
        path = await reply.download()
        check_file_size(path)

        character = {
            "name": character_name,
            "anime": anime,
            "rarity": rarity_text,
            "id": available_id,
            "slock": "false",
            "added": message.from_user.id,
        }

        image_url = await upload_image_with_fallback(path)
        character["img_url"] = image_url

        await collection.insert_one(character)

        caption = (
            f"🌟 **Character Detail** 🌟\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🔹 **Name:** {character_name}\n"
            f"🔸 **Anime:** {anime}\n"
            f"🔹 **ID:** {available_id}\n"
            f"🔸 **Rarity:** {rarity_text}\n"
            f"Added by [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
        )

        # Try to send using the uploaded URL first, fallback to local file
        try:
            if path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".gif")):
                tempo = await client.send_video(chat_id=CHARA_CHANNEL_ID, video=image_url, caption=caption)
            else:
                tempo = await client.send_photo(chat_id=CHARA_CHANNEL_ID, photo=image_url, caption=caption)
        except Exception:
            if path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".gif")):
                tempo = await client.send_video(chat_id=CHARA_CHANNEL_ID, video=path, caption=caption)
            else:
                tempo = await client.send_photo(chat_id=CHARA_CHANNEL_ID, photo=path, caption=caption)

        try:
            await tempo.pin()
        except Exception:
            # pin may fail if bot lacks permissions; ignore
            pass

        await message.reply_text(f"✅ CHARACTER ADDED SUCCESSFULLY! ID: {available_id}")
        # mention or notify update chat if needed
        await client.send_message(chat_id=CHARA_CHANNEL_ID, text=f'@naruto_dev `/sendone {available_id}`')

    except Exception as e:
        error_msg = f"❌ Character Upload Unsuccessful. Error: {e}"
        await message.reply_text(error_msg)
        print(error_msg)
    finally:
        if path and os.path.exists(path):
            os.remove(path)
        if available_id:
            async with id_lock:
                active_ids.discard(available_id)

# Keep delete / update / arrange / other handlers - but ensure they use the imported collection and user_collection.
# ... (the rest of your existing handlers can remain, but make sure to replace any `@app.on_message` with `@shivuu.on_message`
# and replace any hardcoded channel IDs with CHARA_CHANNEL_ID or other imported constants as appropriate.)
#
# Example: converting an app.on_message to shivuu.on_message (delete handler)
#
@shivuu.on_message(filters.command('delete') & sudo_filter)
async def delete(client: shivuu.__class__, message: Message):
    args = message.text.split(maxsplit=1)[1:]
    if len(args) != 1:
        await message.reply_text('Incorrect format... Please use: /delete ID')
        return

    character_id = args[0]
    character = await collection.find_one_and_delete({'id': character_id})

    if character:
        bulk_operations = []
        async for user in user_collection.find():
            if 'characters' in user:
                user['characters'] = [char for char in user['characters'] if char.get('id') != character_id]
                bulk_operations.append(
                    UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
                )

        if bulk_operations:
            await user_collection.bulk_write(bulk_operations)

        await message.reply_text('Character deleted from database and all user collections.')
    else:
        await message.reply_text('Character not found in database.')

# PTB CommandHandlers can still be registered with the shared `application`:
async def check_total_characters(update: Update, context: CallbackContext) -> None:
    try:
        total_characters = await collection.count_documents({})
        await update.message.reply_text(f"Total number of characters: {total_characters}")
    except Exception as e:
        await update.message.reply_text(f"Error occurred: {e}")

application.add_handler(CommandHandler("total", check_total_characters))