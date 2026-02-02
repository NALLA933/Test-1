import os
import asyncio
import aiohttp
from typing import Optional
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from pyrogram import filters, Client
from pyrogram.types import Message
from pymongo import UpdateOne
from shivu import (
    shivuu,
    application,
    collection,
    user_collection,
    user_totals_collection,
    top_global_groups_collection,
    db,
    CHARA_CHANNEL_ID,
    OWNER_ID,
    SUDO_USERS,
    SUPPORT_CHAT,
    UPDATE_CHAT,
)

IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "6d52008ec9026912f9f50c8ca96a09c3")

WRONG_FORMAT_TEXT = """Wrong ❌ format...  eg. /upload reply to photo muzan-kibutsuji Demon-slayer 3

format:- /upload reply character-name anime-name rarity-number

use rarity number accordingly rarity Map

RARITY_MAP = {
    1: (1, "⚪ ᴄᴏᴍᴍᴏɴ"),
    2: (2, "🔵 ʀᴀʀᴇ"),
    3: (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: (4, "💮 ꜱᴘᴇᴄɪᴀʟ"),
    5: (5, "👹 ᴀɴᴄɪᴀᴇɴᴛ"),
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
    3: (3, "🟡 ʟᴇɴᴇɴᴅᴀʀʏ"),
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

active_ids = set()
id_lock = asyncio.Lock()

def sudo_filter_func(_, __, message: Message):
    if not message.from_user:
        return False
    return message.from_user.id in ([OWNER_ID] + [u for u in SUDO_USERS if u != OWNER_ID])

def uploader_filter_func(_, __, message: Message):
    if not message.from_user:
        return False
    return message.from_user.id in ([OWNER_ID] + [u for u in SUDO_USERS if u != OWNER_ID])

sudo_filter = filters.create(sudo_filter_func)
uploader_filter = filters.create(uploader_filter_func)

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
        from telegraph import upload_file
    except Exception:
        raise Exception("Telegraph package not installed. Install it with: pip install telegraph")
    try:
        result = upload_file(file_path)
        if isinstance(result, list) and len(result) > 0:
            return f"https://telegra.ph{result[0]}"
        raise Exception("Telegraph upload failed: upload_file returned no URL")
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

@shivuu.on_message(filters.command(["uid"]) & uploader_filter)
async def ulo(client: Client, message: Message):
    available_id = await find_available_ids()
    await client.send_message(chat_id=message.chat.id, text=f"{available_id}")

@shivuu.on_message(filters.command(["upload"]) & uploader_filter)
async def ul(client: Client, message: Message):
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
            pass
        await message.reply_text(f"✅ CHARACTER ADDED SUCCESSFULLY! ID: {available_id}")
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

@shivuu.on_message(filters.command('delete') & sudo_filter)
async def delete(client: Client, message: Message):
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

async def check_total_characters(update: Update, context: CallbackContext) -> None:
    try:
        total_characters = await collection.count_documents({})
        await update.message.reply_text(f"Total number of characters: {total_characters}")
    except Exception as e:
        await update.message.reply_text(f"Error occurred: {e}")

application.add_handler(CommandHandler("total", check_total_characters))

async def check(update: Update, context: CallbackContext) -> None:
    try:
        args = context.args
        if len(context.args) != 1:
            await update.message.reply_text('Incorrect format. Please use: /check id')
            return
        character_id = context.args[0]
        character = await collection.find_one({'id': character_id})
        if character:
            message_text = f"<b>Character Name:</b> {character['name']}\n" \
                      f"<b>Anime Name:</b> {character['anime']}\n" \
                      f"<b>Rarity:</b> {character['rarity']}\n" \
                      f"<b>ID:</b> {character['id']}\n"
            if 'img_url' in character:
                await context.bot.send_photo(chat_id=update.effective_chat.id,
                                             photo=character['img_url'],
                                             caption=message_text,
                                             parse_mode='HTML')
            elif 'vid_url' in character:
                await context.bot.send_video(chat_id=update.effective_chat.id,
                                             video=character['vid_url'],
                                             caption=message_text,
                                             parse_mode='HTML')
        else:
             await update.message.reply_text("Character not found.")
    except Exception as e:
        await update.message.reply_text(f"Error occurred: {e}")

CHECK_HANDLER = CommandHandler('f', check, block=False)
application.add_handler(CHECK_HANDLER)

@shivuu.on_message(filters.command('update') & uploader_filter)
async def update(client: Client, message: Message):
    args = message.text.split(maxsplit=3)[1:]
    if len(args) != 3:
        await message.reply_text('Incorrect format. Please use: /update id field new_value')
        return
    character_id = args[0]
    field = args[1]
    new_value = args[2]
    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return
    valid_fields = ['img_url', 'name', 'anime', 'rarity']
    if field not in valid_fields:
        await message.reply_text(f'Invalid field. Please use one of the following: {", ".join(valid_fields)}')
        return
    if field in ['name', 'anime']:
        new_value = new_value.replace('-', ' ').title()
    elif field == 'rarity':
        try:
            new_value = RARITY_MAP[int(new_value)][1]
        except Exception:
            await message.reply_text('Invalid rarity. Please use a number between 1 and 15.')
            return
    await collection.update_one({'id': character_id}, {'$set': {field: new_value}})
    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] == character_id:
                    char[field] = new_value
            bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )
    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)
    await message.reply_text('Update done in Database and all user collections.')

@shivuu.on_message(filters.command('r') & sudo_filter)
async def update_rarity(client: Client, message: Message):
    args = message.text.split(maxsplit=2)[1:]
    if len(args) != 2:
        await message.reply_text('Incorrect format. Please use: /r id rarity')
        return
    character_id = args[0]
    new_rarity = args[1]
    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return
    try:
        new_rarity_value = RARITY_MAP[int(new_rarity)][1]
    except Exception:
        await message.reply_text('Invalid rarity. Please use a number between 1 and 15.')
        return
    await collection.update_one({'id': character_id}, {'$set': {'rarity': new_rarity_value}})
    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] == character_id:
                    char['rarity'] = new_rarity_value
            bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )
    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)
    await message.reply_text('Rarity updated in Database and all user collections.')

@shivuu.on_message(filters.command('arrange') & sudo_filter)
async def arrange_characters(client: Client, message: Message):
    characters = await collection.find().sort('id', 1).to_list(length=None)
    if not characters:
        await message.reply_text('No characters found in the database.')
        return
    old_to_new_id_map = {}
    new_id_counter = 1
    bulk_operations = []
    for character in characters:
        old_id = character['id']
        new_id = str(new_id_counter).zfill(2)
        old_to_new_id_map[old_id] = new_id
        if old_id != new_id:
            bulk_operations.append(
                UpdateOne({'_id': character['_id']}, {'$set': {'id': new_id}})
            )
        new_id_counter += 1
    if bulk_operations:
        await collection.bulk_write(bulk_operations)
    user_bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            for char in user['characters']:
                if char['id'] in old_to_new_id_map:
                    char['id'] = old_to_new_id_map[char['id']]
            user_bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )
    if user_bulk_operations:
        await user_collection.bulk_write(user_bulk_operations)
    await message.reply_text('Characters have been rearranged and IDs updated successfully.')

@shivuu.on_message(filters.command("vadd") & uploader_filter)
async def upload_video_character(client: Client, message: Message):
    args = message.text.split(maxsplit=3)
    if len(args) != 4:
        await message.reply_text("Wrong format. Use: /vadd character-name anime-name video-url")
        return
    character_name = args[1].replace('-', ' ').title()
    anime = args[2].replace('-', ' ').title()
    vid_url = args[3]
    available_id = await find_available_id()
    character = {
        'name': character_name,
        'anime': anime,
        'rarity': "🎗️ 𝘼𝙈𝙑 𝙀𝙙𝙞𝙩𝙞𝙤𝙣",
        'id': available_id,
        'vid_url': vid_url,
        'slock': "false",
        'added': message.from_user.id
    }
    try:
        await client.send_video(
            chat_id=CHARA_CHANNEL_ID,
            video=vid_url,
            caption=(
                f"🎥 **New Character Added** 🎥\n\n"
                f"Character Name: {character_name}\n"
                f"Anime Name: {anime}\n"
                f"Rarity: '🎗️ 𝘼𝙈𝙑 𝙀𝙙𝙞𝙩𝙞𝙤𝙣'\n"
                f"ID: {available_id}\n"
                f"Added by [{message.from_user.first_name}](tg://user?id={message.from_user.id})"
            ),
        )
        await collection.insert_one(character)
        await message.reply_text("✅ Video character added successfully.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to upload character. Error: {e}")

@shivuu.on_message(filters.command(["updateimg"]) & uploader_filter)
async def update_image(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document with this command.")
        return
    args = message.text.split()
    if len(args) != 2:
        await message.reply_text("Wrong format. Use: /updateimg [character_id] (reply to image)")
        return
    character_id = args[1]
    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text(f"Character with ID {character_id} not found.")
        return
    try:
        processing_message = await message.reply("<ᴜᴘᴅᴀᴛɪɴɢ ɪᴍᴀɢᴇ...>")
        path = await reply.download()
        check_file_size(path)
        image_url = await upload_image_with_fallback(path)
        await collection.update_one(
            {'id': character_id},
            {'$set': {'img_url': image_url}}
        )
        bulk_operations = []
        async for user in user_collection.find():
            if 'characters' in user:
                for char in user['characters']:
                    if char['id'] == character_id:
                        char['img_url'] = image_url
                bulk_operations.append(
                    UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
                )
        if bulk_operations:
            await user_collection.bulk_write(bulk_operations)
        await message.reply_text(f'✅ Image updated successfully for character ID: {character_id}')
        caption = (
            f"🔄 **Character Image Updated** 🔄\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🔹 **Name:** {character['name']}\n"
            f"🔸 **Anime:** {character['anime']}\n"
            f"🔹 **ID:** {character_id}\n"
            f"🔸 **Rarity:** {character['rarity']}\n"
            f"Image updated by [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
        )
        try:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(chat_id=CHARA_CHANNEL_ID, video=image_url, caption=caption)
            else:
                await client.send_photo(chat_id=CHARA_CHANNEL_ID, photo=image_url, caption=caption)
        except Exception:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(chat_id=CHARA_CHANNEL_ID, video=path, caption=caption)
            else:
                await client.send_photo(chat_id=CHARA_CHANNEL_ID, photo=path, caption=caption)
    except Exception as e:
        error_msg = f"❌ Image update failed. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)
    finally:
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)