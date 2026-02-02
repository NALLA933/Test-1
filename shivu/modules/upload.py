import urllib.request
from pymongo import ReturnDocument
import os
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
import requests
from pyrogram import filters
from pyrogram.types import InputMediaPhoto
import os
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import ReturnDocument, UpdateOne
import urllib.request
import random
import aiohttp
import asyncio

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

# Log the channel ID for debugging
print(f"📢 Using CHARA_CHANNEL_ID: {CHARA_CHANNEL_ID}")

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
        # Use the synchronous telegraph upload function
        result = upload_file(file_path)
        if isinstance(result, list) and len(result) > 0:
            return f"https://telegra.ph{result[0]}"
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
        return str(max(int_ids) + 1).zfill(2)

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

async def verify_channel_access(client: Client):
    """
    Verify if bot has access to the channel
    """
    try:
        chat = await client.get_chat(CHARA_CHANNEL_ID)
        print(f"✅ Channel access verified!")
        print(f"   Title: {chat.title}")
        print(f"   Type: {chat.type}")
        return True
    except Exception as e:
        print(f"❌ Cannot access channel {CHARA_CHANNEL_ID}")
        print(f"   Error: {str(e)}")
        print(f"\n⚠️  SOLUTION:")
        print(f"   1. Add bot as admin in channel")
        print(f"   2. Give 'Post Messages' permission")
        print(f"   3. Verify channel ID is correct")
        return False

@app.on_message(filters.command('upload') & uploader_filter)
async def upload(client: Client, message: Message):
    """
    Upload a new character to the database
    """
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        await message.reply_text("Please reply to a photo or document with this command.")
        return

    args = message.text.split()[1:]
    if len(args) != 3:
        await message.reply_text(WRONG_FORMAT_TEXT)
        return

    character_name = args[0].replace('-', ' ').title()
    anime = args[1].replace('-', ' ').title()

    try:
        rarity_input = int(args[2])
        if rarity_input not in RARITY_MAP:
            await message.reply_text(WRONG_FORMAT_TEXT)
            return
        rarity = RARITY_MAP[rarity_input][1]
    except (ValueError, IndexError):
        await message.reply_text(WRONG_FORMAT_TEXT)
        return

    # Generate the next available ID
    available_id = await find_available_id()

    try:
        processing_message = await message.reply("<ᴜᴘʟᴏᴀᴅɪɴɢ ʏᴏᴜʀ ᴄʜᴀʀᴀᴄᴛᴇʀ...>")

        # Download the file
        path = await reply.download()

        # Check file size
        check_file_size(path)

        # Upload image with fallback (imgBB as primary)
        image_url = await upload_image_with_fallback(path)

        # Create character document
        character = {
            'img_url': image_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity,
            'id': available_id,
            'slock': "false",
            'added': message.from_user.id
        }

        # Insert into database
        await collection.insert_one(character)
        
        await processing_message.edit_text("<ᴜᴘʟᴏᴀᴅɪɴɢ ᴛᴏ ᴄʜᴀɴɴᴇʟ...>")

        # Send to character channel using CHARA_CHANNEL_ID from config
        caption = (
            f"✨ **New Character Added** ✨\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🔹 **Name:** {character_name}\n"
            f"🔸 **Anime:** {anime}\n"
            f"🔹 **ID:** {available_id}\n"
            f"🔸 **Rarity:** {rarity}\n"
            f"Added by [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
            f"\n━━━━━━━━━━━━━━━━━━\n"
        )

        # Send to channel with better error handling
        try:
            print(f"📤 Attempting to send to channel: {CHARA_CHANNEL_ID}")
            
            # First verify channel access
            if not await verify_channel_access(client):
                raise Exception(
                    f"Bot doesn't have access to channel {CHARA_CHANNEL_ID}. "
                    f"Please add bot as admin with 'Post Messages' permission."
                )
            
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
            print(f"✅ Successfully sent to channel")
            
        except Exception as channel_error:
            print(f"❌ Failed to send to channel with URL: {channel_error}")
            print(f"🔄 Trying with local file...")
            
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
            print(f"✅ Successfully sent with local file")

        # Update processing message
        await processing_message.edit_text(
            f'✅ Character Upload Successful.\n'
            f'ID: {available_id}\n'
            f'Channel: {CHARA_CHANNEL_ID}'
        )

    except Exception as e:
        error_msg = f"❌ Character Upload Unsuccessful. Error: {str(e)}"
        await message.reply_text(error_msg)
        print(error_msg)  # Log the error for debugging
        print(f"\n🔍 Debug Info:")
        print(f"   Channel ID: {CHARA_CHANNEL_ID}")
        print(f"   User ID: {message.from_user.id}")
        print(f"   Is in SUDO_USERS: {message.from_user.id in SUDO_USERS}")

    finally:
        # Clean up
        async with id_lock:
            active_ids.discard(available_id)
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)


# Test command to verify channel access
@app.on_message(filters.command('testchannel') & sudo_filter)
async def test_channel(client: Client, message: Message):
    """Test if bot can access the channel"""
    try:
        chat = await client.get_chat(CHARA_CHANNEL_ID)
        
        # Get bot's status in the channel
        bot_member = await client.get_chat_member(CHARA_CHANNEL_ID, "me")
        
        response = (
            f"✅ **Channel Access Test**\n\n"
            f"📢 **Channel Info:**\n"
            f"   • Title: {chat.title}\n"
            f"   • ID: {CHARA_CHANNEL_ID}\n"
            f"   • Type: {chat.type}\n\n"
            f"🤖 **Bot Status:**\n"
            f"   • Status: {bot_member.status}\n"
            f"   • Can post: {bot_member.privileges.can_post_messages if hasattr(bot_member, 'privileges') else 'Unknown'}\n"
        )
        
        await message.reply_text(response)
        
        # Try sending a test message
        test_msg = await client.send_message(
            chat_id=CHARA_CHANNEL_ID,
            text="🧪 Test message from bot - access verified!"
        )
        await message.reply_text("✅ Test message sent successfully to channel!")
        
    except Exception as e:
        error_response = (
            f"❌ **Channel Access Failed**\n\n"
            f"**Error:** {str(e)}\n\n"
            f"**Channel ID:** {CHARA_CHANNEL_ID}\n\n"
            f"**Solutions:**\n"
            f"1. Add bot as admin in channel\n"
            f"2. Give 'Post Messages' permission\n"
            f"3. Verify channel ID is correct\n"
            f"4. Use /getchannelid command"
        )
        await message.reply_text(error_response)


# Command to get channel ID
@app.on_message(filters.command('getchannelid') & sudo_filter)
async def get_channel_id_cmd(client: Client, message: Message):
    """Get channel ID by forwarding a message from the channel"""
    
    if message.reply_to_message and message.reply_to_message.forward_from_chat:
        chat = message.reply_to_message.forward_from_chat
        response = (
            f"📢 **Channel Information**\n\n"
            f"**Title:** {chat.title}\n"
            f"**ID:** `{chat.id}`\n"
            f"**Type:** {chat.type}\n"
            f"**Username:** @{chat.username if chat.username else 'Private'}\n\n"
            f"Copy this ID to your config.py:\n"
            f"`CHARA_CHANNEL_ID = {chat.id}`"
        )
        await message.reply_text(response)
    else:
        await message.reply_text(
            "❌ Please forward a message from your channel and reply to it with /getchannelid"
        )


# Rest of the commands remain the same...
@app.on_message(filters.command('delete') & sudo_filter)
async def delete(client: Client, message: Message):
    args = message.text.split()[1:]
    if len(args) != 1:
        await message.reply_text('Incorrect format. Please use: /delete id')
        return

    character_id = args[0]

    character = await collection.find_one({'id': character_id})
    if not character:
        await message.reply_text('Character not found.')
        return

    await collection.delete_one({'id': character_id})

    # Remove from all user collections
    bulk_operations = []
    async for user in user_collection.find():
        if 'characters' in user:
            user['characters'] = [char for char in user['characters'] if char['id'] != character_id]
            bulk_operations.append(
                UpdateOne({'_id': user['_id']}, {'$set': {'characters': user['characters']}})
            )

    if bulk_operations:
        await user_collection.bulk_write(bulk_operations)

    await message.reply_text('Done')


def check(update: Update, context: CallbackContext) -> None:
    args = context.args
    if len(args) != 1:
        update.message.reply_text('Incorrect format. Please use: /f id')
        return

    character_id = args[0]

    character = collection.find_one({'id': character_id})
    if character:
        update.message.reply_text(f'Character {character["name"]} found with ID {character_id}.')
    else:
        update.message.reply_text('Character not found.')


CHECK_HANDLER = CommandHandler('f', check, block=False)
application.add_handler(CHECK_HANDLER)
