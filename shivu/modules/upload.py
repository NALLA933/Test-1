import os
import random
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaPhoto
from pymongo import ReturnDocument, UpdateOne
from telegraph.aio import Telegraph

# Import from __init__.py
from shivu import (
    collection,
    user_collection,
    shivuu as app,
    application,
    CHARA_CHANNEL_ID,
    SUPPORT_CHAT,
    UPDATE_CHAT,
    OWNER_ID,
    SUDO_USERS,
)

# Define filters for sudo users
def sudo_filter_func(_, __, message):
    """Filter for sudo users (owner and sudo users)"""
    if not message.from_user:
        return False
    return message.from_user.id in SUDO_USERS

def uploader_filter_func(_, __, message):
    """Filter for uploader users (same as sudo for now)"""
    if not message.from_user:
        return False
    return message.from_user.id in SUDO_USERS

sudo_filter = filters.create(sudo_filter_func)
uploader_filter = filters.create(uploader_filter_func)

# Your imgBB API Key
IMGBB_API_KEY = "6d52008ec9026912f9f50c8ca96a09c3"

# Define the wrong format message and rarity map
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

# Define the RARITY_MAP
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

# Global set to keep track of active IDs and a lock for safe access
active_ids = set()
id_lock = asyncio.Lock()

# Telegraph client initialization
telegraph_client = Telegraph()

async def upload_to_imgbb(file_path, api_key=IMGBB_API_KEY):
    """
    Upload image to imgBB (primary upload service)
    """
    url = "https://api.imgbb.com/1/upload"

    # Read the file
    with open(file_path, "rb") as file:
        file_data = file.read()

    # Create form data
    data = aiohttp.FormData()
    data.add_field('key', api_key)
    data.add_field('image', file_data, filename=os.path.basename(file_path))

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            result = await response.json()

            if response.status == 200 and result.get("success"):
                return result["data"]["url"]
            else:
                error_msg = result.get('error', {}).get('message', 'Unknown error')
                raise Exception(f"ImgBB upload failed: {error_msg}")

async def upload_to_telegraph(file_path):
    """
    Upload image to Telegraph (fallback option)
    """
    try:
        # Ensure telegraph client is created
        if not hasattr(telegraph_client, 'token'):
            await telegraph_client.create_account(short_name='CharacterBot')
        
        # Upload file
        with open(file_path, 'rb') as f:
            result = await telegraph_client.upload_file(f)
        
        if isinstance(result, list) and len(result) > 0:
            return f"https://telegra.ph{result[0]['src']}"
        else:
            raise Exception("Telegraph upload failed")
    except Exception as e:
        raise Exception(f"Telegraph upload error: {str(e)}")

async def upload_to_catbox(file_path):
    """
    Upload image to Catbox (secondary fallback option)
    """
    url = "https://catbox.moe/user/api.php"

    # Read the file
    with open(file_path, "rb") as file:
        file_data = file.read()

    # Create form data
    data = aiohttp.FormData()
    data.add_field('reqtype', 'fileupload')
    data.add_field('fileToUpload', file_data, filename=os.path.basename(file_path))

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as response:
            if response.status == 200:
                return (await response.text()).strip()
            else:
                raise Exception(f"Catbox upload failed with status {response.status}")

async def upload_image_with_fallback(file_path):
    """
    Try multiple image hosting services with fallback - imgBB as primary
    """
    services = [
        upload_to_imgbb,  # Primary - imgBB
        upload_to_telegraph,  # First fallback - Telegraph
        upload_to_catbox,  # Second fallback - Catbox
    ]

    last_error = None
    for service in services:
        try:
            print(f"Trying {service.__name__}...")
            url = await service(file_path)
            print(f"Success with {service.__name__}: {url}")
            return url
        except Exception as e:
            print(f"Failed with {service.__name__}: {str(e)}")
            last_error = e
            continue

    raise Exception(f"All image hosting services failed. Last error: {str(last_error)}")

def check_file_size(file_path, max_size_mb=30):
    """
    Check if file size is within limits
    """
    file_size = os.path.getsize(file_path)
    if file_size > max_size_mb * 1024 * 1024:
        raise Exception(f"File size ({file_size/1024/1024:.2f} MB) exceeds the {max_size_mb} MB limit.")
    return True

async def find_available_id():
    """
    Find the next available ID for a character
    """
    async with id_lock:
        cursor = collection.find().sort('id', 1)
        ids = [doc['id'] for doc in await cursor.to_list(length=None)]

        # Handle case where no documents exist
        if not ids:
            candidate_id = "01"
            active_ids.add(candidate_id)
            return candidate_id

        # Convert to integers for proper comparison
        int_ids = [int(id) for id in ids]

        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                active_ids.add(candidate_id)
                return candidate_id
        
        # Fallback if all checked IDs are taken
        new_id = str(max(int_ids) + 1).zfill(2)
        active_ids.add(new_id)
        return new_id

async def find_available_ids():
    """
    Find available IDs without reserving them
    """
    async with id_lock:
        cursor = collection.find().sort('id', 1)
        ids = [doc['id'] for doc in await cursor.to_list(length=None)]

        # Handle case where no documents exist
        if not ids:
            return "01"

        # Convert to integers for proper comparison
        int_ids = [int(id) for id in ids]

        available = []
        for i in range(1, max(int_ids) + 2):
            candidate_id = str(i).zfill(2)
            if candidate_id not in ids and candidate_id not in active_ids:
                available.append(candidate_id)
                if len(available) >= 10:
                    break

        if not available:
            available.append(str(max(int_ids) + 1).zfill(2))

        return ", ".join(available)

async def release_id(character_id):
    """
    Release an ID from the active set
    """
    async with id_lock:
        active_ids.discard(character_id)

@app.on_message(filters.command(["upload"]) & uploader_filter)
async def upload(client, message):
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document with this command.")
        return

    args = message.text.split()
    if len(args) != 4:
        await message.reply_text(WRONG_FORMAT_TEXT)
        return

    character_name = args[1].replace('-', ' ').title()
    anime = args[2].replace('-', ' ').title()
    
    try:
        rarity_number = int(args[3])
        if rarity_number not in RARITY_MAP:
            await message.reply_text("❌ Rarity number should be between 1 and 15.")
            return
        rarity = RARITY_MAP[rarity_number][1]
    except ValueError:
        await message.reply_text("❌ Rarity should be a number.")
        return

    available_id = None
    path = None
    
    try:
        # Generate the next available ID
        available_id = await find_available_id()
        
        processing_message = await message.reply("Processing your request...")

        # Download the image
        path = await reply.download()

        # Check file size
        check_file_size(path)

        # Upload image with fallback (imgBB as primary)
        image_url = await upload_image_with_fallback(path)

        character = {
            'name': character_name,
            'anime': anime,
            'rarity': rarity,
            'id': available_id,
            'img_url': image_url,
            'slock': "false",
            'added': message.from_user.id
        }

        # Insert the character into the database
        await collection.insert_one(character)

        # Send to character channel
        caption = (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>Character Name</b>: {character_name}\n"
            f"<b>Anime Name</b>: {anime}\n"
            f"<b>Rarity</b>: {rarity}\n"
            f"<b>ID</b>: {available_id}\n"
            f"Added by <a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        # Try to send with URL, fallback to local file
        try:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=image_url,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=image_url,
                    caption=caption,
                )
        except:
            # Fallback to local file
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=path,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=path,
                    caption=caption,
                )

        await processing_message.edit("✅ Character added successfully!")
        
        # Release the ID from active set after successful upload
        await release_id(available_id)

    except Exception as e:
        # Release the ID if upload failed
        if available_id:
            await release_id(available_id)
        
        error_msg = f"❌ Upload failed. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)

    finally:
        # Clean up downloaded file
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass

@app.on_message(filters.command(["delete", "del"]) & sudo_filter)
async def delete(client, message):
    args = message.text.split()
    if len(args) != 2:
        await message.reply_text("❌ Incorrect format. Please use: /delete id")
        return

    character_id = args[1]
    character = await collection.find_one({'id': character_id})
    
    if not character:
        await message.reply_text("❌ Character not found.")
        return

    # Delete from main collection
    await collection.delete_one({'id': character_id})

    # Remove from all users
    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            original_count = len(user['characters'])
            user['characters'] = [char for char in user['characters'] if char['id'] != character_id]
            
            if len(user['characters']) != original_count:
                bulk_operations.append(
                    UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
                )

    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)

    await message.reply_text(f"✅ Character {character_id} deleted successfully from database and all users.")

@app.on_message(filters.command('ids') & sudo_filter)
async def show_available_ids(client: Client, message: Message):
    available = await find_available_ids()
    await message.reply_text(f"Available IDs: {available}")

async def check(update, context):
    """Check character info (for PTB handler)"""
    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format. Please use: /f id')
            return

        character_id = args[0]
        character = await collection.find_one({'id': character_id})
        
        if not character:
            await update.message.reply_text('Character not found.')
            return

        name = character.get('name', 'Unknown')
        anime = character.get('anime', 'Unknown')
        rarity = character.get('rarity', 'Unknown')
        img_url = character.get('img_url', '')

        message_text = (
            f"<b>Character Name:</b> {name}\n"
            f"<b>Anime:</b> {anime}\n"
            f"<b>Rarity:</b> {rarity}\n"
            f"<b>ID:</b> {character_id}\n"
        )

        if img_url:
            await update.message.reply_photo(photo=img_url, caption=message_text, parse_mode='HTML')
        else:
            await update.message.reply_text(message_text, parse_mode='HTML')

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

@app.on_message(filters.command('u') & sudo_filter)
async def update_character(client: Client, message: Message):
    args = message.text.split(maxsplit=3)[1:]
    if len(args) != 3:
        await message.reply_text('Incorrect format. Please use: /u id field new_value')
        return

    character_id, field, new_value = args

    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return

    valid_fields = ['img_url', 'name', 'anime', 'rarity']
    if field not in valid_fields:
        await message.reply_text(f'Invalid field. Valid fields are: {", ".join(valid_fields)}')
        return

    # Update in main collection
    await collection.update_one({'id': character_id}, {'$set': {field: new_value}})

    # Update in all user collections
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

    await message.reply_text(f'✅ Updated {field} for character {character_id}')

@app.on_message(filters.command('r') & sudo_filter)
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
    except (KeyError, ValueError):
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

    await message.reply_text('✅ Rarity updated in Database and all user collections.')

@app.on_message(filters.command('arrange') & sudo_filter)
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

    await message.reply_text('✅ Characters have been rearranged and IDs updated successfully.')

# PTB handler for check command
from telegram.ext import CommandHandler
CHECK_HANDLER = CommandHandler('f', check, block=False)
application.add_handler(CHECK_HANDLER)

@app.on_message(filters.command("vadd") & uploader_filter)
async def upload_video_character(client, message):
    args = message.text.split(maxsplit=3)
    if len(args) != 4:
        await message.reply_text("Wrong format. Use: /vadd character-name anime-name video-url")
        return

    character_name = args[1].replace('-', ' ').title()
    anime = args[2].replace('-', ' ').title()
    vid_url = args[3]

    available_id = None
    
    try:
        # Generate the next available ID
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

        # Send the video to the character channel
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

        # Insert the character data into MongoDB
        await collection.insert_one(character)
        
        # Release ID after successful upload
        await release_id(available_id)

        await message.reply_text("✅ Video character added successfully.")
        
    except Exception as e:
        # Release the ID if upload failed
        if available_id:
            await release_id(available_id)
        
        await message.reply_text(f"❌ Failed to upload character. Error: {e}")

@app.on_message(filters.command(["updateimg"]) & uploader_filter)
async def update_image(client, message):
    """
    Command to update character image by replying to a photo with the character ID
    Format: /updateimg [character_id]
    """
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document with this command.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.reply_text("Wrong format. Use: /updateimg [character_id] (reply to image)")
        return

    character_id = args[1]

    # Check if character exists
    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text(f"Character with ID {character_id} not found.")
        return

    path = None
    processing_message = None
    
    try:
        processing_message = await message.reply("<ᴜᴘᴅᴀᴛɪɴɢ ɪᴍᴀɢᴇ...>")

        # Download the new image
        path = await reply.download()

        # Check file size
        check_file_size(path)

        # Upload image with fallback (imgBB as primary)
        image_url = await upload_image_with_fallback(path)

        # Update character in the database
        await collection.update_one(
            {'id': character_id}, 
            {'$set': {'img_url': image_url}}
        )

        # Update all user collections that have this character
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

        # Send confirmation message
        await message.reply_text(f'✅ Image updated successfully for character ID: {character_id}')

        # Send updated character info to channel
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

        # Try to send with the uploaded URL
        try:
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=image_url,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=image_url,
                    caption=caption,
                )
        except:
            # Fallback to sending the local file if URL doesn't work
            if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.gif')):
                await client.send_video(
                    chat_id=CHARA_CHANNEL_ID,
                    video=path,
                    caption=caption,
                )
            else:
                await client.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=path,
                    caption=caption,
                )

    except Exception as e:
        error_msg = f"❌ Image update failed. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)

    finally:
        # Clean up downloaded file
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass