"""
Anime Character Management Bot with Fail-Safe Image System
Requirements:
- python-telegram-bot v22+
- aiohttp
- Motor (async MongoDB)
- Strict image handling with Catbox.moe
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple, Union
from urllib.parse import urlparse
from datetime import datetime

import aiohttp
from pymongo import ReturnDocument
from telegram import Update, PhotoSize, InputMediaPhoto
from telegram.ext import CommandHandler, ContextTypes, Application
from telegram.error import BadRequest, TelegramError

# Import your existing configuration
from shivu.config import Config
from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

CATBOX_API_URL = "https://catbox.moe/user/api.php"
CATBOX_UPLOAD_TIMEOUT = 30
CATBOX_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB (Catbox limit)

# Rarity mapping (from your existing code)
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

VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']

# Error messages
ERROR_CATBOX_UNAVAILABLE = "❌ Image upload failed (Catbox server unavailable). Try again later."
ERROR_TELEGRAM_DOWNLOAD = "❌ Failed to download image from Telegram. Please try again."
ERROR_INVALID_URL = "❌ Invalid image URL. Must be a publicly accessible image (HTTP 200, image/* content-type)."
ERROR_DATABASE_CONSISTENCY = "⚠️ Database operation failed. Please check bot logs."

# UI Messages
WRONG_FORMAT_TEXT = """❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ!

📌 ʜᴏᴡ ᴛᴏ ᴜꜱᴇ /upload:

ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ

ꜱᴇɴᴅ ᴛʜᴇ ᴄᴏᴍᴍᴀɴᴅ /upload
ɪɴᴄʟᴜᴅᴇ 3 ʟɪɴᴇꜱ ɪɴ ʏᴏᴜʀ ᴍᴇꜱꜱᴀɢᴇ:

ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ 
ᴀɴɪᴍᴇ ɴᴀᴍᴇ 
ʀᴀʀɪᴛʏ (1-15)

✨ ᴇxᴀᴍᴘʟᴇ:
/upload 
ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ 
ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ 
4

📊 ʀᴀʀɪᴛʏ ᴍᴀᴘ (1-15):

• 1 ⚪ ᴄᴏᴍᴍᴏɴ 
• 2 🔵 ʀᴀʀᴇ 
• 3 🟡 ʟᴇɢᴇɴᴅᴀʀʏ 
• 4 💮 ꜱᴘᴇᴄɪᴀʟ 
• 5 👹 ᴀɴᴄɪᴇɴᴛ 
• 6 🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ 
• 7 🔮 ᴇᴘɪᴄ 
• 8 🪐 ᴄᴏꜱᴍɪᴄ 
• 9 ⚰️ ɴɪɢʜᴛᴍᴀʀᴇ 
• 10 🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ 
• 11 💝 ᴠᴀʟᴇɴᴛɪɴᴇ 
• 12 🌸 ꜱᴘʀɪɴɢ 
• 13 🏖️ ᴛʀᴏᴘɪᴄᴀʟ 
• 14 🍭 ᴋᴀᴡᴀɪɪ 
• 15 🧬 ʜʏʙʀɪᴅ"""

# ============================================================================
# GLOBAL SESSION MANAGEMENT
# ============================================================================

SESSION: Optional[aiohttp.ClientSession] = None

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================================
# CORE UTILITY FUNCTIONS
# ============================================================================

def format_character_id(sequence_number: int) -> str:
    """Format character ID from sequence number."""
    return str(sequence_number)


async def get_next_sequence_number(sequence_name: str) -> int:
    """Get next sequence number from MongoDB."""
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['sequence_value']


def get_best_photo_file_id(photo_sizes: List[PhotoSize]) -> str:
    """Get the highest resolution photo file_id."""
    return photo_sizes[-1].file_id


def extract_filename_from_url(url: str) -> str:
    """Extract filename from URL for upload."""
    parsed = urlparse(url)
    path = parsed.path
    if path:
        name = path.split('/')[-1]
        if '.' in name:
            return name
    return "image.jpg"


def format_update_help(fields: list) -> str:
    """Format update command help message."""
    return (
        "📝 ᴜᴘᴅᴀᴛᴇ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
        "ᴜᴘᴅᴀᴛᴇ ᴡɪᴛʜ ᴠᴀʟᴜᴇ:\n"
        "/update ɪᴅ ꜰɪᴇʟᴅ ɴᴇᴡᴠᴀʟᴜᴇ\n\n"
        "ᴜᴘᴅᴀᴛᴇ ɪᴍᴀɢᴇ (ʀᴇᴘʟʏ ᴛᴏ ᴘʜᴏᴛᴏ):\n"
        "/update ɪᴅ ɪᴍɢ_ᴜʀʟ\n\n"
        "ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ:\n"
        "ɪᴍɢ_ᴜʀʟ, ɴᴀᴍᴇ, ᴀɴɪᴍᴇ, ʀᴀʀɪᴛʏ\n\n"
        "ᴇxᴀᴍᴘʟᴇꜱ:\n"
        "/update 12 ɴᴀᴍᴇ ɴᴇᴢᴜᴋᴏ ᴋᴀᴍᴀᴅᴏ\n"
        "/update 12 ᴀɴɪᴍᴇ ᴅᴇᴍᴏɴ ꜱʟᴀʏᴇʀ\n"
        "/update 12 ʀᴀʀɪᴛʏ 5\n"
        "/update 12 ɪᴍɢ_ᴜʀʟ ʀᴇᴘʟʏ_ɪᴍɢ"
    )


async def get_session() -> aiohttp.ClientSession:
    """Get or create aiohttp session with proper settings."""
    global SESSION
    if SESSION is None or SESSION.closed:
        # Use connection pooling for better performance
        connector = aiohttp.TCPConnector(
            limit=10,  # Max 10 concurrent connections
            limit_per_host=5,
            ttl_dns_cache=300
        )
        timeout = aiohttp.ClientTimeout(
            total=CATBOX_UPLOAD_TIMEOUT,
            connect=10,
            sock_read=25
        )
        SESSION = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'AnimeBot/1.0'}
        )
    return SESSION


async def cleanup_session() -> None:
    """Cleanup aiohttp session on shutdown."""
    global SESSION
    if SESSION and not SESSION.closed:
        await SESSION.close()
        SESSION = None


# ============================================================================
# IMAGE HANDLING CORE FUNCTIONS
# ============================================================================

async def download_telegram_file(file_id: str, bot) -> bytes:
    """
    Download file from Telegram and return bytes.
    Raises TelegramError on failure.
    """
    try:
        logger.info(f"Downloading Telegram file: {file_id[:20]}...")
        file = await bot.get_file(file_id)
        
        # Download in chunks to handle large files
        buffer = bytearray()
        async for chunk in file.download_as_bytearray():
            buffer.extend(chunk)
            
        if not buffer:
            raise TelegramError("Downloaded file is empty")
            
        logger.info(f"Downloaded {len(buffer)} bytes from Telegram")
        return bytes(buffer)
        
    except TelegramError as e:
        logger.error(f"Telegram download failed for {file_id[:20]}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error downloading file: {e}")
        raise TelegramError(f"Download failed: {str(e)}")


async def upload_to_catbox(image_bytes: bytes, filename: str = "image.jpg") -> Optional[str]:
    """
    Upload image to Catbox.moe and return URL.
    Returns None on failure.
    """
    session = await get_session()
    
    # Validate file size
    if len(image_bytes) > CATBOX_MAX_FILE_SIZE:
        logger.error(f"File too large for Catbox: {len(image_bytes)} bytes")
        return None
    
    # Prepare form data for Catbox API
    data = aiohttp.FormData()
    data.add_field('reqtype', 'fileupload')
    data.add_field(
        'fileToUpload',
        image_bytes,
        filename=filename,
        content_type='image/jpeg'
    )
    
    try:
        logger.info(f"Uploading {len(image_bytes)} bytes to Catbox as {filename}")
        
        async with session.post(CATBOX_API_URL, data=data) as response:
            if response.status == 200:
                url = (await response.text()).strip()
                
                # Validate Catbox URL format
                if url and url.startswith('https://files.catbox.moe/'):
                    logger.info(f"Catbox upload successful: {url}")
                    return url
                else:
                    logger.error(f"Catbox returned invalid URL: {url}")
                    return None
            else:
                logger.error(f"Catbox upload failed with status {response.status}")
                return None
                
    except asyncio.TimeoutError:
        logger.error("Catbox upload timed out")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"Catbox upload client error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during Catbox upload: {e}")
        return None


async def validate_image_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate an image URL.
    Returns (is_valid, content_type or error_message).
    """
    session = await get_session()
    
    try:
        # Parse URL to ensure it's valid
        parsed = urlparse(url)
        if not parsed.scheme in ('http', 'https'):
            return False, "URL must use http or https protocol"
        
        # HEAD request to check content type (more efficient)
        async with session.head(url, allow_redirects=True, timeout=10) as resp:
            if resp.status != 200:
                return False, f"HTTP status {resp.status}"
            
            content_type = resp.headers.get('Content-Type', '').lower()
            if not content_type.startswith('image/'):
                return False, f"Invalid content type: {content_type}"
            
            # Check file size if available
            content_length = resp.headers.get('Content-Length')
            if content_length and int(content_length) > CATBOX_MAX_FILE_SIZE:
                return False, f"File too large: {content_length} bytes"
            
            return True, content_type
            
    except asyncio.TimeoutError:
        return False, "Connection timeout"
    except aiohttp.ClientError as e:
        return False, f"HTTP error: {str(e)}"
    except ValueError:
        return False, "Invalid URL format"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


async def atomic_image_upload(
    bot,
    file_id: str,
    existing_character: Optional[Dict] = None
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Atomic operation: Download from Telegram AND upload to Catbox.
    Returns (character_data, error_message).
    
    STRICT RULE: Both operations must succeed or fail together.
    """
    try:
        # Step 1: Download from Telegram
        logger.info(f"Starting atomic upload for file_id: {file_id[:20]}")
        image_bytes = await download_telegram_file(file_id, bot)
        
        if not image_bytes:
            return None, ERROR_TELEGRAM_DOWNLOAD
        
        # Step 2: Upload to Catbox
        catbox_url = await upload_to_catbox(image_bytes)
        
        if not catbox_url:
            return None, ERROR_CATBOX_UNAVAILABLE
        
        # Step 3: Prepare character data with BOTH file_id and URL
        character_data = {
            'img_file_id': file_id,
            'img_url': catbox_url,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Preserve existing fields if updating
        if existing_character:
            for key in ['name', 'anime', 'rarity', 'id', 'message_id', 'created_at']:
                if key in existing_character:
                    character_data[key] = existing_character[key]
        
        logger.info("Atomic upload completed successfully")
        return character_data, None
        
    except TelegramError as e:
        logger.error(f"Telegram error in atomic upload: {e}")
        return None, f"❌ Telegram error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error in atomic upload: {e}")
        return None, f"❌ Unexpected error: {str(e)}"


async def atomic_image_update(
    bot,
    character_id: str,
    new_file_id: str
) -> Tuple[bool, Optional[str]]:
    """
    Atomic image update with rollback on failure.
    Returns (success, error_message).
    """
    try:
        # Get existing character
        character = await collection.find_one({'id': character_id})
        if not character:
            return False, "Character not found"
        
        # Perform atomic upload
        character_data, error = await atomic_image_upload(bot, new_file_id, character)
        if error:
            return False, error
        
        # Update database with BOTH file_id and URL
        result = await collection.update_one(
            {'id': character_id},
            {
                '$set': {
                    'img_file_id': character_data['img_file_id'],
                    'img_url': character_data['img_url'],
                    'updated_at': character_data['updated_at']
                }
            }
        )
        
        if result.modified_count == 0:
            return False, "Database update failed"
            
        return True, None
        
    except Exception as e:
        logger.error(f"Atomic image update failed: {e}")
        return False, str(e)


# ============================================================================
# CHANNEL MESSAGE MANAGEMENT
# ============================================================================

async def send_channel_message(
    context: ContextTypes.DEFAULT_TYPE, 
    character: Dict[str, Any], 
    user_id: int, 
    user_name: str,
    action: str = "Added"
) -> Optional[int]:
    """
    Send or update message in character channel.
    STRICT RULE: Prefer file_id, fall back to URL.
    """
    try:
        caption = (
            f"<b>Character Name:</b> {character['name']}\n"
            f"<b>Anime Name:</b> {character['anime']}\n"
            f"<b>Rarity:</b> {character['rarity']}\n"
            f"<b>ID:</b> {character['id']}\n"
            f"{action} by <a href='tg://user?id={user_id}'>{user_name}</a>"
        )

        # STRICT RULE: Prefer Telegram file_id for channel messages
        if character.get('img_file_id'):
            photo_source = character['img_file_id']
            logger.info(f"Sending to channel with file_id: {photo_source[:20]}...")
        elif character.get('img_url'):
            photo_source = character['img_url']
            logger.info(f"Sending to channel with URL: {photo_source}")
        else:
            raise ValueError("No valid image source available for channel message")

        bot = context.bot
        
        if action == "Added" or 'message_id' not in character:
            # Send new message
            message = await bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=photo_source,
                caption=caption,
                parse_mode='HTML'
            )
            logger.info(f"Sent new message to channel: {message.message_id}")
            return message.message_id
            
        else:
            # Update existing message
            # Check if we need to update photo or just caption
            current_character = await collection.find_one({'id': character['id']})
            if not current_character:
                raise ValueError("Character not found in database")
            
            # If image source changed, we need to delete and recreate
            current_source = current_character.get('img_file_id') or current_character.get('img_url')
            new_source = character.get('img_file_id') or character.get('img_url')
            
            if current_source != new_source:
                logger.info("Image source changed, recreating message")
                try:
                    await bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id']
                    )
                except BadRequest:
                    pass  # Message might already be deleted
                
                message = await bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=photo_source,
                    caption=caption,
                    parse_mode='HTML'
                )
                return message.message_id
            else:
                # Only caption changed
                await bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    caption=caption,
                    parse_mode='HTML'
                )
                logger.info(f"Updated caption for message: {character['message_id']}")
                return character['message_id']
                
    except BadRequest as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "message to edit not found" in error_msg:
            # Recreate message if it doesn't exist
            logger.warning("Channel message not found, recreating")
            photo_source = character.get('img_file_id') or character.get('img_url')
            if not photo_source:
                raise ValueError("No valid image source available")

            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=photo_source,
                caption=caption,
                parse_mode='HTML'
            )
            return message.message_id
        raise
    except Exception as e:
        logger.error(f"Failed to send channel message: {e}")
        raise


# ============================================================================
# COMMAND HANDLERS
# ============================================================================

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /upload command handler.
    STRICT RULES:
    1. Must reply to a Telegram photo
    2. Must upload to Catbox successfully
    3. Must store BOTH file_id and URL
    4. No partial database inserts allowed
    """
    # Permission check
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
        return

    # Check for reply to photo
    if not (update.message.reply_to_message and update.message.reply_to_message.photo):
        await update.message.reply_text(
            "📸 ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\n"
            "ʏᴏᴜ ᴍᴜꜱᴛ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴡɪᴛʜ ᴛʜᴇ /upload ᴄᴏᴍᴍᴀɴᴅ."
        )
        return

    try:
        # Parse command text
        text_content = update.message.text or update.message.caption or ""
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        
        if lines and lines[0].startswith('/upload'):
            lines = lines[1:]

        if len(lines) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        char_raw, anime_raw, rarity_raw = lines

        # Get photo file_id
        photo_sizes = update.message.reply_to_message.photo
        img_file_id = get_best_photo_file_id(photo_sizes)

        # Validate rarity
        try:
            rarity_num = int(rarity_raw.strip())
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ ɴᴜᴍʙᴇʀ!\n\nᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 15.'
                )
                return
            rarity = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text(
                f'❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ!\n\nʏᴏᴜ ᴇɴᴛᴇʀᴇᴅ: "{rarity_raw}"'
            )
            return

        # STRICT RULE: Atomic upload to Catbox (MUST SUCCEED)
        await update.message.reply_text("⏳ Downloading image and uploading to Catbox...")
        
        character_data, error = await atomic_image_upload(context.bot, img_file_id)
        
        if error:
            # STRICT RULE: Do NOT proceed if Catbox fails
            await update.message.reply_text(error)
            return

        # Add character metadata
        character_data.update({
            'name': char_raw.title(),
            'anime': anime_raw.title(),
            'rarity': rarity,
            'id': format_character_id(await get_next_sequence_number('character_id')),
            'created_at': datetime.utcnow().isoformat(),
            'added_by': {
                'user_id': update.effective_user.id,
                'username': update.effective_user.username or update.effective_user.first_name
            }
        })

        # Send to channel (using file_id, not URL)
        try:
            message_id = await send_channel_message(
                context, character_data,
                update.effective_user.id,
                update.effective_user.first_name,
                "Added"
            )
            character_data['message_id'] = message_id
            
        except BadRequest as e:
            error_msg = str(e).lower()
            if "chat not found" in error_msg or "not enough rights" in error_msg:
                await update.message.reply_text(
                    "❌ Cannot send to channel. Bot might not have permission."
                )
                return
            raise

        # FINAL STEP: Insert into database (only after everything succeeded)
        await collection.insert_one(character_data)
        
        await update.message.reply_text(
            f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n'
            f'• ɴᴀᴍᴇ: {character_data["name"]}\n'
            f'• ᴀɴɪᴍᴇ: {character_data["anime"]}\n'
            f'• ʀᴀʀɪᴛʏ: {character_data["rarity"]}\n'
            f'• ɪᴅ: {character_data["id"]}\n'
            f'• 📁 ᴛᴇʟᴇɢʀᴀᴍ ꜰɪʟᴇ_ɪᴅ: {character_data["img_file_id"][:20]}...\n'
            f'• 🌐 ᴄᴀᴛʙᴏx ᴜʀʟ: {character_data["img_url"]}'
        )

    except Exception as e:
        logger.error(f"Upload command failed: {e}", exc_info=True)
        await update.message.reply_text(
            f'❌ ᴜᴘʟᴏᴀᴅ ꜰᴀɪʟᴇᴅ!\n\nᴇʀʀᴏʀ: {str(e)[:200]}\n\n'
            f'ɪꜰ ᴛʜɪꜱ ᴇʀʀᴏʀ ᴘᴇʀꜱɪꜱᴛꜱ, ᴄᴏɴᴛᴀᴄᴛ: {SUPPORT_CHAT}'
        )


async def update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /update command handler.
    STRICT RULES:
    Mode A (Reply-to-photo): Must upload to Catbox, store BOTH file_id and URL
    Mode B (URL): Must validate URL, store only URL with file_id: null
    """
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('ʏᴏᴜ ᴅᴏ ɴᴏᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ.')
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            format_update_help(VALID_FIELDS),
            parse_mode='Markdown'
        )
        return

    char_id = context.args[0]
    field = context.args[1]

    if field not in VALID_FIELDS:
        await update.message.reply_text(
            f'❌ ɪɴᴠᴀʟɪᴅ ꜰɪᴇʟᴅ. ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ: {", ".join(VALID_FIELDS)}'
        )
        return

    # Get existing character
    character = await collection.find_one({'id': char_id})
    if not character:
        await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
        return

    # Handle img_url field updates
    if field == 'img_url':
        # Check if user is replying to a photo (Mode A)
        if len(context.args) == 2:
            if not (update.message.reply_to_message and update.message.reply_to_message.photo):
                await update.message.reply_text(
                    '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\n'
                    'ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url'
                )
                return

            # MODE A: Reply-to-photo update
            photo_sizes = update.message.reply_to_message.photo
            new_file_id = get_best_photo_file_id(photo_sizes)
            
            await update.message.reply_text("⏳ Uploading new image to Catbox...")
            
            # ATOMIC UPLOAD: Get new file_id and Catbox URL
            success, error = await atomic_image_update(context.bot, char_id, new_file_id)
            
            if not success:
                # STRICT RULE: Do NOT update if Catbox fails
                await update.message.reply_text(f"❌ {error}")
                return
            
            # Get updated character for channel message
            updated_character = await collection.find_one({'id': char_id})
            photo_source = updated_character['img_file_id']  # Use file_id for channel
            
        else:
            # MODE B: URL-based update
            new_url = context.args[2]
            
            # Validate URL
            is_valid, error = await validate_image_url(new_url)
            if not is_valid:
                await update.message.reply_text(
                    f'❌ {ERROR_INVALID_URL}\n\nᴅᴇᴛᴀɪʟꜱ: {error}'
                )
                return
            
            # Update with URL only (file_id becomes null)
            update_data = {
                'img_url': new_url,
                'img_file_id': None,
                'updated_at': datetime.utcnow().isoformat()
            }
            
            updated_character = await collection.find_one_and_update(
                {'id': char_id},
                {'$set': update_data},
                return_document=ReturnDocument.AFTER
            )
            
            if not updated_character:
                await update.message.reply_text('❌ Failed to update database.')
                return
            
            photo_source = new_url  # Use URL for channel
        
    elif field in ['name', 'anime']:
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ ᴍɪꜱꜱɪɴɢ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id {field} new_value'
            )
            return
        
        new_value = context.args[2]
        update_data = {
            field: new_value.replace('-', ' ').title(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        updated_character = await collection.find_one_and_update(
            {'id': char_id},
            {'$set': update_data},
            return_document=ReturnDocument.AFTER
        )
        
        if not updated_character:
            await update.message.reply_text('❌ Failed to update database.')
            return
        
        photo_source = updated_character.get('img_file_id') or updated_character.get('img_url')
        
    elif field == 'rarity':
        if len(context.args) != 3:
            await update.message.reply_text(
                f'❌ ᴍɪꜱꜱɪɴɢ ʀᴀʀɪᴛʏ ᴠᴀʟᴜᴇ. ᴜꜱᴀɢᴇ: /update id rarity 1-15'
            )
            return
        
        new_value = context.args[2]
        try:
            rarity_num = int(new_value)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ ɪɴᴠᴀʟɪᴅ ʀᴀʀɪᴛʏ. ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴀ ɴᴜᴍʙᴇʀ ʙᴇᴛᴡᴇᴇɴ 1 ᴀɴᴅ 15.'
                )
                return
            update_data = {
                'rarity': RARITY_MAP[rarity_num],
                'updated_at': datetime.utcnow().isoformat()
            }
        except ValueError:
            await update.message.reply_text('❌ ʀᴀʀɪᴛʏ ᴍᴜꜱᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ (1-15).')
            return
        
        updated_character = await collection.find_one_and_update(
            {'id': char_id},
            {'$set': update_data},
            return_document=ReturnDocument.AFTER
        )
        
        if not updated_character:
            await update.message.reply_text('❌ Failed to update database.')
            return
        
        photo_source = updated_character.get('img_file_id') or updated_character.get('img_url')
        
    else:
        await update.message.reply_text('❌ ᴜɴᴋɴᴏᴡɴ ꜰɪᴇʟᴅ.')
        return

    # Update channel message
    try:
        if field == 'img_url':
            # For image updates, we need to replace the entire message
            if 'message_id' in character:
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id']
                    )
                except BadRequest:
                    pass  # Message might already be deleted
            
            # Send new message
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=photo_source,
                caption=(
                    f"<b>Character Name:</b> {updated_character['name']}\n"
                    f"<b>Anime Name:</b> {updated_character['anime']}\n"
                    f"<b>Rarity:</b> {updated_character['rarity']}\n"
                    f"<b>ID:</b> {updated_character['id']}\n"
                    f"Updated by <a href='tg://user?id={update.effective_user.id}'>"
                    f"{update.effective_user.first_name}</a>"
                ),
                parse_mode='HTML'
            )
            
            # Update message_id in database
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': message.message_id}}
            )
            
        elif 'message_id' in updated_character:
            # For non-image updates, edit caption
            await context.bot.edit_message_caption(
                chat_id=CHARA_CHANNEL_ID,
                message_id=updated_character['message_id'],
                caption=(
                    f"<b>Character Name:</b> {updated_character['name']}\n"
                    f"<b>Anime Name:</b> {updated_character['anime']}\n"
                    f"<b>Rarity:</b> {updated_character['rarity']}\n"
                    f"<b>ID:</b> {updated_character['id']}\n"
                    f"Updated by <a href='tg://user?id={update.effective_user.id}'>"
                    f"{update.effective_user.first_name}</a>"
                ),
                parse_mode='HTML'
            )
        
        await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')
        
    except BadRequest as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "message to edit not found" in error_msg:
            # Recreate message if it doesn't exist
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=photo_source,
                caption=(
                    f"<b>Character Name:</b> {updated_character['name']}\n"
                    f"<b>Anime Name:</b> {updated_character['anime']}\n"
                    f"<b>Rarity:</b> {updated_character['rarity']}\n"
                    f"<b>ID:</b> {updated_character['id']}\n"
                    f"Updated by <a href='tg://user?id={update.effective_user.id}'>"
                    f"{update.effective_user.first_name}</a>"
                ),
                parse_mode='HTML'
            )
            
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': message.message_id}}
            )
            
            await update.message.reply_text(
                '✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ! (ʀᴇᴄʀᴇᴀᴛᴇᴅ ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ)'
            )
        else:
            await update.message.reply_text(
                f'✅ ᴅᴀᴛᴀʙᴀꜱᴇ ᴜᴘᴅᴀᴛᴇᴅ ʙᴜᴛ ᴄʜᴀɴɴᴇʟ ᴜᴘᴅᴀᴛᴇ ꜰᴀɪʟᴇᴅ: {str(e)[:100]}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ ᴅᴀᴛᴀʙᴀꜱᴇ ᴜᴘᴅᴀᴛᴇᴅ ʙᴜᴛ ᴄʜᴀɴɴᴇʟ ᴜᴘᴅᴀᴛᴇ ꜰᴀɪʟᴇᴅ: {str(e)[:100]}'
        )


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete character from database and channel."""
    if update.effective_user.id not in Config.SUDO_USERS:
        await update.message.reply_text('ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ...')
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text('❌ ɪɴᴄᴏʀʀᴇᴄᴛ ꜰᴏʀᴍᴀᴛ... ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ: /delete ID')
        return

    character_id = context.args[0]

    character = await collection.find_one_and_delete({'id': character_id})

    if not character:
        await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.')
        return

    try:
        if 'message_id' in character:
            await context.bot.delete_message(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id']
            )
            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ ᴀɴᴅ ᴄʜᴀɴɴᴇʟ.')
        else:
            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ɴᴏ ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ꜰᴏᴜɴᴅ).')
    except BadRequest as e:
        error_msg = str(e).lower()
        if "message to delete not found" in error_msg:
            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ (ᴄʜᴀɴɴᴇʟ ᴍᴇꜱꜱᴀɢᴇ ᴡᴀꜱ ᴀʟʀᴇᴀᴅʏ ɢᴏɴᴇ).')
        else:
            await update.message.reply_text(
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ᴅᴇʟᴇᴛᴇ ꜰʀᴏᴍ ᴄʜᴀɴɴᴇʟ: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n⚠️ ᴄʜᴀɴɴᴇʟ ᴅᴇʟᴇᴛɪᴏɴ ᴇʀʀᴏʀ: {str(e)}'
        )


# ============================================================================
# DATABASE MIGRATION & INTEGRITY CHECKS
# ============================================================================

async def verify_database_integrity():
    """Check database for consistency and log issues."""
    logger.info("Starting database integrity check...")
    
    # Find characters with incomplete image data
    incomplete_count = await collection.count_documents({
        '$or': [
            {'img_file_id': {'$exists': False}},
            {'img_url': {'$exists': False}},
            {'img_file_id': None, 'img_url': None}
        ]
    })
    
    if incomplete_count > 0:
        logger.warning(f"Found {incomplete_count} characters with incomplete image data")
        
        async for character in collection.find({
            '$or': [
                {'img_file_id': {'$exists': False}},
                {'img_url': {'$exists': False}},
                {'img_file_id': None, 'img_url': None}
            ]
        }):
            logger.warning(f"Incomplete character: ID={character.get('id')}, "
                          f"file_id={character.get('img_file_id')}, "
                          f"url={character.get('img_url')}")
    
    logger.info("Database integrity check completed")


async def migrate_existing_characters():
    """
    Migrate existing characters to have both file_id and Catbox URL.
    Run this once to update your database.
    """
    logger.info("Starting character migration...")
    
    async for character in collection.find({
        'img_file_id': {'$exists': True, '$ne': None},
        'img_url': {'$exists': False}
    }):
        try:
            # Download and upload to Catbox
            bot = application.bot
            file_id = character['img_file_id']
            
            logger.info(f"Migrating character {character['id']}: {character.get('name', 'Unnamed')}")
            
            character_data, error = await atomic_image_upload(bot, file_id, character)
            
            if character_data and character_data.get('img_url'):
                await collection.update_one(
                    {'_id': character['_id']},
                    {'$set': {'img_url': character_data['img_url']}}
                )
                logger.info(f"✓ Migrated {character['id']}")
            else:
                logger.warning(f"✗ Failed to migrate {character['id']}: {error}")
                
            await asyncio.sleep(1)  # Rate limiting
            
        except Exception as e:
            logger.error(f"Error migrating character {character['id']}: {e}")
            await asyncio.sleep(2)  # Longer delay on error
    
    logger.info("Migration complete!")


# ============================================================================
# APPLICATION SETUP
# ============================================================================

def setup_handlers(app: Application):
    """Register command handlers."""
    app.add_handler(CommandHandler("upload", upload))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("update", update))


async def startup_tasks(app: Application):
    """Run startup tasks."""
    logger.info("Bot starting up...")
    await verify_database_integrity()
    
    # Optional: Run migration if needed
    # await migrate_existing_characters()


async def shutdown_tasks(app: Application):
    """Run shutdown tasks."""
    logger.info("Bot shutting down...")
    await cleanup_session()


# ============================================================================
# MAIN SETUP
# ============================================================================

# Register handlers with your application
setup_handlers(application)

# Add startup and shutdown hooks
application.add_handler(CommandHandler("upload", upload))
application.add_handler(CommandHandler("delete", delete))
application.add_handler(CommandHandler("update", update))

# If you have access to the Application's post_init/post_stop:
# application.post_init = startup_tasks
# application.post_stop = shutdown_tasks

logger.info("Image system initialized with strict safety rules")