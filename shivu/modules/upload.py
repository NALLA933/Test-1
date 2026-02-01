import asyncio
import hashlib
import html
import io
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, ASCENDING
from pymongo.errors import DuplicateKeyError
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto, InputMediaDocument
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config


# ===================== LOGGING CONFIGURATION =====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ===================== SETUP FUNCTION =====================
async def setup_database_indexes():
    """Create database indexes for optimal performance"""
    try:
        # Unique index on character ID
        await collection.create_index([("id", ASCENDING)], unique=True, background=True)

        # Regular index on file_hash for fast lookups
        await collection.create_index([("file_hash", ASCENDING)], background=True)

        # Index on rarity for filtering
        await collection.create_index([("rarity", ASCENDING)], background=True)

        # Index on uploader_id for user queries
        await collection.create_index([("uploader_id", ASCENDING)], background=True)

        logger.info("✅ Database indexes created successfully")
    except Exception as e:
        logger.error(f"⚠️ Failed to create indexes: {e}")


# ===================== ENUMS =====================

class MediaType(Enum):
    """Allowed media types"""
    PHOTO = "photo"
    DOCUMENT = "document"
    VIDEO = "video"
    ANIMATION = "animation"

    @classmethod
    def from_telegram_message(cls, message) -> Optional['MediaType']:
        """Detect media type from Telegram message"""
        if message.photo:
            return cls.PHOTO
        elif message.document:
            mime_type = message.document.mime_type or ''
            if mime_type.startswith('image/'):
                return cls.DOCUMENT
        elif message.video:
            return cls.VIDEO
        elif message.animation:
            return cls.ANIMATION
        return None


class RarityLevel(Enum):
    """Rarity levels (1-15) matching Code A"""
    COMMON = (1, "⚪ ᴄᴏᴍᴍᴏɴ")
    RARE = (2, "🔵 ʀᴀʀᴇ")
    LEGENDARY = (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ")
    SPECIAL = (4, "💮 ꜱᴘᴇᴄɪᴀʟ")
    ANCIENT = (5, "👹 ᴀɴᴄɪᴇɴᴛ")
    CELESTIAL = (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ")
    EPIC = (7, "🔮 ᴇᴘɪᴄ")
    COSMIC = (8, "🪐 ᴄᴏꜱᴍɪᴄ")
    NIGHTMARE = (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ")
    FROSTBORN = (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ")
    VALENTINE = (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ")
    SPRING = (12, "🌸 ꜱᴘʀɪɴɢ")
    TROPICAL = (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ")
    KAWAII = (14, "🍭 ᴋᴀᴡᴀɪɪ")
    HYBRID = (15, "🧬 ʜʏʙʀɪᴅ")

    def __init__(self, level: int, display: str):
        self._level = level
        self._display = display

    @property
    def level(self) -> int:
        return self._level

    @property
    def display_name(self) -> str:
        return self._display

    @classmethod
    def from_number(cls, num: int) -> Optional['RarityLevel']:
        for rarity in cls:
            if rarity.level == num:
                return rarity
        return None

    @classmethod
    def get_all(cls) -> Dict[int, str]:
        """Get all rarity levels as dict (matching Code A format)"""
        return {rarity.level: rarity.display_name for rarity in cls}


# ===================== DATACLASSES =====================

@dataclass(frozen=True)
class BotConfig:
    """Bot configuration"""
    MAX_FILE_SIZE: int = 20 * 1024 * 1024
    DOWNLOAD_TIMEOUT: int = 300
    UPLOAD_TIMEOUT: int = 300
    CHUNK_SIZE: int = 65536
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0
    CONNECTION_LIMIT: int = 100
    CATBOX_API: str = "https://catbox.moe/user/api.php"
    TELEGRAPH_API: str = "https://telegra.ph/upload"
    ALLOWED_MIME_TYPES: Tuple[str, ...] = (
        'image/jpeg', 'image/png', 'image/webp', 'image/jpg'
    )


@dataclass
class MediaFile:
    """Represents a media file with efficient memory handling"""
    file_path: Optional[str] = None
    media_type: Optional[MediaType] = None
    filename: str = field(default="")
    mime_type: Optional[str] = None
    size: int = 0
    hash: str = field(default="")
    catbox_url: Optional[str] = None
    telegram_file_id: Optional[str] = None

    def __post_init__(self):
        # Note: Hash computation will be done asynchronously via compute_hash_async()
        if self.file_path and not self.size:
            import os
            object.__setattr__(self, 'size', os.path.getsize(self.file_path))

    def _compute_hash_sync(self) -> str:
        """BLOCKING sync hash computation - DO NOT call directly in async code"""
        sha256_hash = hashlib.sha256()
        if self.file_path:
            with open(self.file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(65536), b""):
                    sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    async def compute_hash_async(self) -> str:
        """
        FIX #2: Non-blocking hash computation using executor
        Offloads blocking file I/O to thread pool
        """
        if self.hash:
            return self.hash
        
        loop = asyncio.get_event_loop()
        computed_hash = await loop.run_in_executor(None, self._compute_hash_sync)
        object.__setattr__(self, 'hash', computed_hash)
        return computed_hash

    @property
    def is_valid_image(self) -> bool:
        """Check if media is a valid image"""
        if self.media_type in [MediaType.VIDEO, MediaType.ANIMATION]:
            return False
        if self.mime_type:
            return self.mime_type.startswith('image/')
        return self.media_type in [MediaType.PHOTO, MediaType.DOCUMENT]

    @property
    def is_valid_size(self) -> bool:
        """Check if file size is within limits"""
        return self.size <= BotConfig.MAX_FILE_SIZE

    def cleanup(self):
        """Clean up temporary file"""
        if self.file_path:
            try:
                import os
                os.unlink(self.file_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup file {self.file_path}: {e}")


@dataclass
class Character:
    """Represents a character entry with integer rarity storage"""
    character_id: str
    name: str
    anime: str
    rarity: int  # Store as integer (1-15)
    media_file: MediaFile
    uploader_id: int
    uploader_name: str
    message_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB storage"""
        return {
            'id': self.character_id,
            'name': self.name,
            'anime': self.anime,
            'rarity': self.rarity,  # Store as integer
            'img_url': self.media_file.catbox_url,
            'message_id': self.message_id,
            'uploader_id': self.uploader_id,
            'uploader_name': self.uploader_name,
            'file_hash': self.media_file.hash,
            'created_at': self.created_at or datetime.utcnow().isoformat(),
            'updated_at': self.updated_at or datetime.utcnow().isoformat()
        }


# ===================== SESSION MANAGER =====================

class SessionManager:
    """
    FIX #3: Added User-Agent header for Telegraph compatibility
    Manages aiohttp session with proper headers to prevent blocking
    """
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
        """Get or create the shared session with proper headers"""
        if cls._session is None or cls._session.closed:
            async with cls._lock:
                if cls._session is None or cls._session.closed:
                    connector = TCPConnector(limit=BotConfig.CONNECTION_LIMIT)
                    
                    # FIX #3: Add browser-like User-Agent to prevent Telegraph blocking
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                    
                    cls._session = ClientSession(
                        connector=connector,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=BotConfig.UPLOAD_TIMEOUT)
                    )
        return cls._session

    @classmethod
    async def close(cls):
        """Close the session"""
        if cls._session and not cls._session.closed:
            await cls._session.close()
            cls._session = None


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handles media file extraction and validation"""

    @staticmethod
    async def extract_from_reply(message: Message) -> Optional[MediaFile]:
        """
        FIX #2 & #4: Improved resource management with proper cleanup
        Extract media file from a message with guaranteed cleanup
        """
        media_type = MediaType.from_telegram_message(message)
        if not media_type:
            return None

        media_file = None
        temp_file = None
        
        try:
            if media_type == MediaType.PHOTO:
                file_obj = message.photo[-1]
                filename = f"photo_{file_obj.file_unique_id}.jpg"
                mime_type = "image/jpeg"
            elif media_type == MediaType.DOCUMENT:
                file_obj = message.document
                filename = file_obj.file_name or f"document_{file_obj.file_unique_id}"
                mime_type = file_obj.mime_type
            else:
                return None

            # Download file
            file_info = await file_obj.get_file()
            
            # Create temp file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
            temp_path = temp_file.name
            temp_file.close()

            await file_info.download_to_drive(temp_path)

            # Create MediaFile object
            media_file = MediaFile(
                file_path=temp_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                telegram_file_id=file_obj.file_id
            )

            # FIX #2: Compute hash asynchronously (non-blocking)
            await media_file.compute_hash_async()

            return media_file

        except Exception as e:
            logger.error(f"Failed to extract media: {e}", exc_info=True)
            # FIX #4: Cleanup on error
            if media_file:
                media_file.cleanup()
            elif temp_file and hasattr(temp_file, 'name'):
                try:
                    import os
                    os.unlink(temp_file.name)
                except:
                    pass
            return None


# ===================== MEDIA UPLOADER =====================

class MediaUploader:
    """Handles media upload to external services (Catbox/Telegraph)"""

    @staticmethod
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """
        Upload file to Catbox with Telegraph fallback
        FIX #3: User-Agent now handled by SessionManager
        """
        # Try Catbox first
        url = await MediaUploader._upload_to_catbox(file_path, filename)
        if url:
            return url

        # Fallback to Telegraph
        logger.info("Catbox failed, trying Telegraph...")
        return await MediaUploader._upload_to_telegraph(file_path)

    @staticmethod
    async def _upload_to_catbox(file_path: str, filename: str) -> Optional[str]:
        """Upload to Catbox.moe with retries"""
        session = await SessionManager.get_session()

        for attempt in range(BotConfig.MAX_RETRIES):
            try:
                data = aiohttp.FormData()
                data.add_field('reqtype', 'fileupload')
                data.add_field('fileToUpload', open(file_path, 'rb'), filename=filename)

                async with session.post(BotConfig.CATBOX_API, data=data) as response:
                    if response.status == 200:
                        url = await response.text()
                        if url and url.startswith('http'):
                            logger.info(f"✅ Catbox upload successful: {url}")
                            return url.strip()

                logger.warning(f"Catbox attempt {attempt + 1} failed: status {response.status}")

            except Exception as e:
                logger.error(f"Catbox attempt {attempt + 1} error: {e}")

            if attempt < BotConfig.MAX_RETRIES - 1:
                await asyncio.sleep(BotConfig.RETRY_DELAY * (attempt + 1))

        return None

    @staticmethod
    async def _upload_to_telegraph(file_path: str) -> Optional[str]:
        """
        Upload to Telegraph with User-Agent support
        FIX #3: User-Agent now in SessionManager headers
        """
        session = await SessionManager.get_session()

        for attempt in range(BotConfig.MAX_RETRIES):
            try:
                data = aiohttp.FormData()
                data.add_field('file', open(file_path, 'rb'))

                async with session.post(BotConfig.TELEGRAPH_API, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if isinstance(result, list) and len(result) > 0:
                            path = result[0].get('src', '')
                            if path:
                                url = f"https://telegra.ph{path}"
                                logger.info(f"✅ Telegraph upload successful: {url}")
                                return url

                logger.warning(f"Telegraph attempt {attempt + 1} failed: status {response.status}")

            except Exception as e:
                logger.error(f"Telegraph attempt {attempt + 1} error: {e}")

            if attempt < BotConfig.MAX_RETRIES - 1:
                await asyncio.sleep(BotConfig.RETRY_DELAY * (attempt + 1))

        return None


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles uploading characters to Telegram channel"""

    @staticmethod
    def format_caption(character: Character) -> str:
        """Format character caption for Telegram"""
        rarity = RarityLevel.from_number(character.rarity)
        rarity_display = rarity.display_name if rarity else f"Rarity {character.rarity}"

        return (
            f"<b>Character Name:</b> {character.name}\n"
            f"<b>Anime Name:</b> {character.anime}\n"
            f"<b>Rarity:</b> {rarity_display}\n"
            f"<b>ID:</b> <code>{character.character_id}</code>\n\n"
            f"<b>Added by:</b> <a href='tg://user?id={character.uploader_id}'>{character.uploader_name}</a>"
        )

    @staticmethod
    async def upload_to_channel(character: Character, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """
        Upload character to channel
        FIX #4: Proper error handling with cleanup
        """
        try:
            caption = TelegramUploader.format_caption(character)

            sent_message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character.media_file.catbox_url,
                caption=caption,
                parse_mode='HTML'
            )

            return sent_message.message_id

        except Exception as e:
            logger.error(f"Failed to upload to channel: {e}", exc_info=True)
            return None

    @staticmethod
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        old_message_id: Optional[int]
    ) -> Optional[int]:
        """
        FIX #1 & #6: Update channel message with proper message_id tracking
        
        Strategy: Try edit_message_caption first (preserves link), 
        fallback to delete+send (returns new message_id for DB update)
        """
        try:
            caption = TelegramUploader.format_caption(character)

            # FIX #6: Try to edit caption first (best approach - no broken links)
            if old_message_id:
                try:
                    await context.bot.edit_message_caption(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=old_message_id,
                        caption=caption,
                        parse_mode='HTML'
                    )
                    logger.info(f"✅ Successfully edited message {old_message_id} caption")
                    # Return same message_id since we edited in place
                    return old_message_id
                
                except BadRequest as e:
                    # If caption edit fails (e.g., photo needs changing), fall through to delete+send
                    logger.warning(f"Caption edit failed, falling back to delete+send: {e}")

            # FIX #1 & #6: Delete old message and send new one
            if old_message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=old_message_id
                    )
                except Exception as e:
                    logger.warning(f"Failed to delete old message {old_message_id}: {e}")

            # Send new message
            sent_message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character.media_file.catbox_url,
                caption=caption,
                parse_mode='HTML'
            )

            # FIX #1: Return the NEW message_id so caller can update DB
            new_message_id = sent_message.message_id
            logger.info(f"✅ Sent new channel message with ID: {new_message_id}")
            return new_message_id

        except Exception as e:
            logger.error(f"Failed to update channel message: {e}", exc_info=True)
            return None


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Factory for creating character instances"""

    @staticmethod
    async def get_next_id() -> str:
        """Get next available character ID"""
        last_character = await collection.find_one(
            sort=[('id', -1)]
        )

        if last_character and 'id' in last_character:
            try:
                last_id = int(last_character['id'])
                return str(last_id + 1)
            except (ValueError, TypeError):
                pass

        # Fallback: count documents
        count = await collection.count_documents({})
        return str(count + 1)

    @staticmethod
    def format_name(text: str) -> str:
        """Format name with HTML escaping and title case"""
        return html.escape(text.strip().title())

    @staticmethod
    async def create_from_upload(
        name: str,
        anime: str,
        rarity: int,
        media_file: MediaFile,
        uploader_id: int,
        uploader_name: str
    ) -> Character:
        """
        Create a character from upload data
        FIX #2: Ensures hash is computed asynchronously
        """
        character_id = await CharacterFactory.get_next_id()
        
        # Ensure hash is computed
        if not media_file.hash:
            await media_file.compute_hash_async()

        return Character(
            character_id=character_id,
            name=CharacterFactory.format_name(name),
            anime=CharacterFactory.format_name(anime),
            rarity=rarity,
            media_file=media_file,
            uploader_id=uploader_id,
            uploader_name=CharacterFactory.format_name(uploader_name),
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat()
        )


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handle /upload command"""

    @staticmethod
    def format_rarity_list() -> str:
        """Format rarity list for display"""
        rarities = RarityLevel.get_all()
        return '\n'.join([f"{num}. {display}" for num, display in rarities.items()])

    @staticmethod
    def format_upload_help() -> str:
        """Format upload command help message"""
        rarity_list = UploadHandler.format_rarity_list()
        return (
            "📝 ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
            "Reply to an image with:\n"
            "`/upload Character_Name : Anime_Name : Rarity_Number`\n\n"
            "ᴇxᴀᴍᴘʟᴇ:\n"
            "`/upload Nezuko Kamado : Demon Slayer : 5`\n\n"
            f"ʀᴀʀɪᴛʏ ʟᴇᴠᴇʟꜱ:\n{rarity_list}"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle /upload command with improved error handling and resource management
        FIX #2, #4, #5: Non-blocking I/O, proper cleanup, atomic duplicate handling
        """
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not update.message.reply_to_message:
            await update.message.reply_text(UploadHandler.format_upload_help())
            return

        if not context.args or len(context.args) < 5:
            await update.message.reply_text(
                '❌ ɪɴᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛ!\n\n' + UploadHandler.format_upload_help()
            )
            return

        media_file = None
        
        try:
            # Parse arguments
            full_text = ' '.join(context.args)
            parts = [p.strip() for p in full_text.split(':')]

            if len(parts) != 3:
                await update.message.reply_text(
                    '❌ Please use format: Name : Anime : Rarity\n\n' +
                    UploadHandler.format_upload_help()
                )
                return

            character_name, anime_name, rarity_str = parts

            # Validate rarity
            try:
                rarity_num = int(rarity_str.strip())
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await update.message.reply_text(
                        f'❌ Invalid rarity. Please use 1-15.\n\n{UploadHandler.format_rarity_list()}'
                    )
                    return
            except ValueError:
                await update.message.reply_text(
                    f'❌ Rarity must be a number.\n\n{UploadHandler.format_rarity_list()}'
                )
                return

            # Extract media
            processing_msg = await update.message.reply_text("🔄 **Processing upload...**")
            
            # FIX #4: Extract media with automatic cleanup on error
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)

            if not media_file or not media_file.is_valid_image:
                await processing_msg.edit_text("❌ Invalid media! Only photos and image documents allowed.")
                return

            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f"❌ File too large! Max size: {BotConfig.MAX_FILE_SIZE / (1024*1024):.1f} MB"
                )
                if media_file:
                    media_file.cleanup()
                return

            # FIX #5: Check for duplicate BEFORE upload (but handle race condition)
            existing = await collection.find_one({'file_hash': media_file.hash})
            if existing:
                await processing_msg.edit_text(
                    f'❌ This image already exists!\n'
                    f'Character ID: {existing.get("id")}\n'
                    f'Name: {existing.get("name")}'
                )
                media_file.cleanup()
                return

            # Create character
            character = await CharacterFactory.create_from_upload(
                name=character_name,
                anime=anime_name,
                rarity=rarity_num,
                media_file=media_file,
                uploader_id=update.effective_user.id,
                uploader_name=update.effective_user.first_name
            )

            # Upload to cloud storage
            await processing_msg.edit_text("☁️ **Uploading to cloud...**")
            catbox_url = await MediaUploader.upload(media_file.file_path, media_file.filename)

            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to cloud storage.")
                media_file.cleanup()
                return

            character.media_file.catbox_url = catbox_url

            # Upload to Telegram channel
            await processing_msg.edit_text("📤 **Uploading to channel...**")
            message_id = await TelegramUploader.upload_to_channel(character, context)

            if not message_id:
                await processing_msg.edit_text("❌ Failed to upload to channel.")
                media_file.cleanup()
                return

            character.message_id = message_id

            # FIX #5: Atomic insert with race condition handling
            try:
                await collection.insert_one(character.to_dict())
                logger.info(f"✅ Character {character.character_id} uploaded successfully")
                
                await processing_msg.edit_text(
                    f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!\n\n'
                    f'🆔 ID: `{character.character_id}`\n'
                    f'📛 Name: {character.name}\n'
                    f'📺 Anime: {character.anime}\n'
                    f'⭐ Rarity: {rarity.display_name}'
                )

            except DuplicateKeyError:
                # FIX #5: Handle race condition - another process inserted the same ID
                logger.warning(f"Duplicate key error for ID {character.character_id} - race condition detected")
                await processing_msg.edit_text(
                    "❌ A duplicate was detected during upload (race condition). Please try again."
                )
                # Try to delete the channel message we just created
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=message_id
                    )
                except:
                    pass

        except Exception as e:
            logger.error(f"Upload failed: {e}", exc_info=True)
            await update.message.reply_text(f'❌ Upload failed: {str(e)}')
        
        finally:
            # FIX #4: ALWAYS cleanup temp files
            if media_file:
                media_file.cleanup()


# ===================== DELETE HANDLER =====================

class DeleteHandler:
    """Handle /delete command"""

    @staticmethod
    def format_delete_help() -> str:
        """Format delete command help message"""
        return (
            "🗑️ ᴅᴇʟᴇᴛᴇ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
            "/delete character_id\n\n"
            "ᴇxᴀᴍᴘʟᴇ:\n"
            "/delete 12"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args:
            await update.message.reply_text(DeleteHandler.format_delete_help())
            return

        char_id = context.args[0]
        character = await collection.find_one({'id': char_id})

        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
            return

        # Delete from channel
        if 'message_id' in character:
            try:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
            except Exception as e:
                logger.warning(f"Failed to delete channel message: {e}")

        # Delete from database
        await collection.delete_one({'id': char_id})

        await update.message.reply_text(
            f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ {char_id} ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!'
        )


# ===================== UPDATE HANDLER =====================

class UpdateHandler:
    """Handle /update command"""
    VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']

    @staticmethod
    def format_update_help() -> str:
        """Format update command help message"""
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

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle /update command with multi-word argument support
        FIX #1: Properly save new message_id after channel update
        FIX #2, #4: Non-blocking I/O and proper cleanup
        """
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(UpdateHandler.format_update_help())
            return

        char_id = context.args[0]
        field = context.args[1]

        if field not in UpdateHandler.VALID_FIELDS:
            await update.message.reply_text(
                f'❌ ɪɴᴠᴀʟɪᴅ ꜰɪᴇʟᴅ. ᴠᴀʟɪᴅ ꜰɪᴇʟᴅꜱ: {", ".join(UpdateHandler.VALID_FIELDS)}'
            )
            return

        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
            return

        update_data = {}
        media_file = None

        try:
            if field == 'img_url':
                if len(context.args) == 2:
                    if not (update.message.reply_to_message and 
                           (update.message.reply_to_message.photo or 
                            update.message.reply_to_message.document)):
                        await update.message.reply_text(
                            '📸 ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ʀᴇǫᴜɪʀᴇᴅ!\n\nʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ: /update id img_url'
                        )
                        return

                    processing_msg = await update.message.reply_text("🔄 **Processing new image...**")

                    # FIX #4: Extract with automatic cleanup
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)

                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                        return

                    # Create character for parallel upload with HTML-escaped data
                    char_for_upload = Character(
                        character_id=character['id'],
                        name=html.escape(character['name']),
                        anime=html.escape(character['anime']),
                        rarity=character['rarity'],  # Already integer
                        media_file=media_file,
                        uploader_id=update.effective_user.id,
                        uploader_name=html.escape(update.effective_user.first_name)
                    )

                    # Step 1: Upload to Catbox/Telegraph first
                    await processing_msg.edit_text("☁️ **Uploading to cloud...**")
                    catbox_url = await MediaUploader.upload(media_file.file_path, media_file.filename)

                    if not catbox_url:
                        await processing_msg.edit_text("❌ Failed to upload to cloud storage.")
                        return

                    # Update character with Catbox URL
                    char_for_upload.media_file.catbox_url = catbox_url

                    # Step 2: Update Telegram channel message
                    await processing_msg.edit_text("📤 **Updating channel...**")
                    new_message_id = await TelegramUploader.update_channel_message(
                        char_for_upload, 
                        context, 
                        character.get('message_id')
                    )

                    # FIX #1: Save the new message_id to database
                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    if new_message_id:
                        update_data['message_id'] = new_message_id

                    await processing_msg.edit_text('✅ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')

                else:
                    # Validate context.args length before accessing
                    if len(context.args) < 3:
                        await update.message.reply_text('❌ Missing image URL. Usage: /update id img_url URL')
                        return

                    new_value = context.args[2]
                    update_data['img_url'] = new_value

            elif field in ['name', 'anime']:
                # Join all remaining arguments for multi-word support
                if len(context.args) < 3:
                    await update.message.reply_text(
                        f'❌ Missing value. Usage: /update id {field} new_value'
                    )
                    return

                # Join all arguments from index 2 onwards for multi-word support
                new_value = " ".join(context.args[2:])
                # Apply HTML escaping and title case formatting
                update_data[field] = CharacterFactory.format_name(new_value)

            elif field == 'rarity':
                # Validate context.args length
                if len(context.args) < 3:
                    await update.message.reply_text(
                        f'❌ Missing rarity value. Usage: /update id rarity 1-15'
                    )
                    return

                new_value = context.args[2]
                try:
                    rarity_num = int(new_value)
                    rarity = RarityLevel.from_number(rarity_num)
                    if not rarity:
                        await update.message.reply_text(
                            f'❌ Invalid rarity. Please use a number between 1 and 15.'
                        )
                        return
                    update_data['rarity'] = rarity_num  # Store as integer
                except ValueError:
                    await update.message.reply_text(f'❌ Rarity must be a number (1-15).')
                    return

            # Update timestamp
            update_data['updated_at'] = datetime.utcnow().isoformat()

            # Update in database
            updated_character = await collection.find_one_and_update(
                {'id': char_id},
                {'$set': update_data},
                return_document=ReturnDocument.AFTER
            )

            if not updated_character:
                await update.message.reply_text('❌ Failed to update character in database.')
                return

            # Update channel message (if not img_url which was already handled)
            if field != 'img_url' and 'message_id' in updated_character:
                try:
                    # Create character object for channel update with HTML-escaped data
                    channel_char = Character(
                        character_id=updated_character['id'],
                        name=html.escape(updated_character['name']),
                        anime=html.escape(updated_character['anime']),
                        rarity=updated_character['rarity'],
                        media_file=MediaFile(catbox_url=updated_character['img_url']),
                        uploader_id=update.effective_user.id,
                        uploader_name=html.escape(update.effective_user.first_name)
                    )

                    # FIX #1: Capture and save new message_id if channel update changes it
                    new_message_id = await TelegramUploader.update_channel_message(
                        channel_char,
                        context,
                        updated_character['message_id']
                    )
                    
                    # FIX #1: If message_id changed (delete+send), update DB
                    if new_message_id and new_message_id != updated_character['message_id']:
                        await collection.update_one(
                            {'id': char_id},
                            {'$set': {'message_id': new_message_id}}
                        )
                        logger.info(f"Updated message_id in DB: {updated_character['message_id']} -> {new_message_id}")
                    
                except Exception as e:
                    logger.warning(f"Failed to update channel message: {e}")
                    pass  # Channel update is optional

            await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')

        except Exception as e:
            logger.error(f"Update failed: {e}", exc_info=True)
            await update.message.reply_text(f'❌ Failed to update: {str(e)}')
        
        finally:
            # FIX #4: ALWAYS cleanup temp files
            if media_file:
                media_file.cleanup()


# ===================== APPLICATION SETUP =====================

async def post_init(application):
    """Initialize database indexes after application starts"""
    await setup_database_indexes()


# Register command handlers with non-blocking option
application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))

# Set up post_init to run setup_database_indexes
application.post_init = post_init


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
    logger.info("Bot shutdown complete")
