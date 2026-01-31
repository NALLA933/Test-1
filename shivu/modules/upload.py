import asyncio
import hashlib
import io
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, ASCENDING
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto, InputMediaDocument
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config


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

        print("✅ Database indexes created successfully")
    except Exception as e:
        print(f"⚠️ Failed to create indexes: {e}")


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
    COMMON = (1, "⚪ ᴄᴏᴍᴍᴏɴ", "⚪", "Common")
    RARE = (2, "🔵 ʀᴀʀᴇ", "🔵", "Rare")
    LEGENDARY = (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ", "🟡", "Legendary")
    SPECIAL = (4, "💮 ꜱᴘᴇᴄɪᴀʟ", "💮", "Special")
    ANCIENT = (5, "👹 ᴀɴᴄɪᴇɴᴛ", "👹", "Ancient")
    CELESTIAL = (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ", "🎐", "Celestial")
    EPIC = (7, "🔮 ᴇᴘɪᴄ", "🔮", "Epic")
    COSMIC = (8, "🪐 ᴄᴏꜱᴍɪᴄ", "🪐", "Cosmic")
    NIGHTMARE = (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ", "⚰️", "Nightmare")
    FROSTBORN = (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ", "🌬️", "Frostborn")
    VALENTINE = (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ", "💝", "Valentine")
    SPRING = (12, "🌸 ꜱᴘʀɪɴɢ", "🌸", "Spring")
    TROPICAL = (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ", "🏖️", "Tropical")
    KAWAII = (14, "🍭 ᴋᴀᴡᴀɪɪ", "🍭", "Kawaii")
    HYBRID = (15, "🧬 ʜʏʙʀɪᴅ", "🧬", "Hybrid")

    def __init__(self, level: int, display: str, emoji: str, name: str):
        self._level = level
        self._display = display
        self._emoji = emoji
        self._name = name

    @property
    def level(self) -> int:
        return self._level

    @property
    def display_name(self) -> str:
        return self._display
    
    @property
    def emoji(self) -> str:
        return self._emoji
    
    @property
    def name(self) -> str:
        return self._name

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
        if self.file_path and not self.hash:
            object.__setattr__(self, 'hash', self._compute_hash())
        if self.file_path and not self.size:
            import os
            object.__setattr__(self, 'size', os.path.getsize(self.file_path))

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of file efficiently"""
        sha256_hash = hashlib.sha256()
        if self.file_path:
            with open(self.file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

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
            except:
                pass


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
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

    def get_caption(self, action: str = "Added") -> str:
        """Generate caption for channel post - NEW FORMAT"""
        rarity_obj = RarityLevel.from_number(self.rarity)
        
        if rarity_obj:
            rarity_emoji = rarity_obj.emoji
            rarity_name = rarity_obj.name
        else:
            rarity_emoji = "❓"
            rarity_name = f"Level {self.rarity}"

        # NEW FORMAT as requested
        return (
            f"{self.character_id}: {self.name}\n"
            f"{self.anime}\n"
            f"{rarity_emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_name}\n\n"
            f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={self.uploader_id}'>{self.uploader_name}</a>"
        )


# ===================== UTILITY CLASSES =====================

class SessionManager:
    """Manages aiohttp client sessions with connection pooling"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
        """Get or create session with connection pooling"""
        if cls._session is None or cls._session.closed:
            async with cls._lock:
                if cls._session is None or cls._session.closed:
                    connector = TCPConnector(
                        limit=BotConfig.CONNECTION_LIMIT,
                        ttl_dns_cache=300,
                        enable_cleanup_closed=True
                    )
                    timeout = aiohttp.ClientTimeout(
                        total=BotConfig.DOWNLOAD_TIMEOUT,
                        connect=30
                    )
                    cls._session = ClientSession(connector=connector, timeout=timeout)
        return cls._session

    @classmethod
    async def close(cls):
        """Close the session"""
        if cls._session and not cls._session.closed:
            await cls._session.close()


class RetryHandler:
    """Handles retry logic with exponential backoff"""

    @staticmethod
    async def execute_with_retry(func, *args, max_retries: int = BotConfig.MAX_RETRIES, **kwargs):
        """Execute function with retry logic"""
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except (NetworkError, TimedOut, aiohttp.ClientError) as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = BotConfig.RETRY_DELAY * (2 ** attempt)
                await asyncio.sleep(wait_time)
        return None


# ===================== MEDIA HANDLING =====================

class MediaHandler:
    """Handles media file extraction and validation"""

    @staticmethod
    async def extract_from_reply(message: Message) -> Optional[MediaFile]:
        """Extract media file from replied message"""
        try:
            media_type = MediaType.from_telegram_message(message)
            if not media_type:
                return None

            if media_type == MediaType.PHOTO:
                photo = message.photo[-1]
                file = await photo.get_file()
                filename = f"photo_{photo.file_unique_id}.jpg"
                mime_type = "image/jpeg"
                file_id = photo.file_id

            elif media_type == MediaType.DOCUMENT:
                doc = message.document
                if not doc.mime_type or not doc.mime_type.startswith('image/'):
                    return None
                file = await doc.get_file()
                filename = doc.file_name or f"document_{doc.file_unique_id}.jpg"
                mime_type = doc.mime_type
                file_id = doc.file_id

            else:
                return None

            # Download to temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            temp_file.close()

            await file.download_to_drive(temp_file.name)

            return MediaFile(
                file_path=temp_file.name,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                telegram_file_id=file_id
            )

        except Exception as e:
            print(f"Media extraction error: {e}")
            return None


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handles Catbox.moe uploads with retry logic"""

    @staticmethod
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload file to Catbox with retry logic"""
        try:
            session = await SessionManager.get_session()

            with open(file_path, 'rb') as f:
                form_data = aiohttp.FormData()
                form_data.add_field('reqtype', 'fileupload')
                form_data.add_field('fileToUpload', f, filename=filename)

                async with session.post(
                        BotConfig.CATBOX_API,
                        data=form_data,
                        timeout=aiohttp.ClientTimeout(total=BotConfig.UPLOAD_TIMEOUT)
                ) as response:
                    if response.status == 200:
                        url = await response.text()
                        return url.strip()
                    return None

        except Exception as e:
            print(f"Catbox upload error: {e}")
            return None


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles Telegram channel uploads"""

    @staticmethod
    async def upload_to_channel(character: Character, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Upload character to channel and return message_id - FIXED TO SEND AS PHOTO"""
        try:
            caption = character.get_caption()
            
            # CHANGE 3: Always send as photo, even if it was a document
            if character.media_file.file_path:
                # Use the local file to send as photo
                with open(character.media_file.file_path, 'rb') as photo_file:
                    message = await context.bot.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=photo_file,
                        caption=caption,
                        parse_mode='HTML'
                    )
            elif character.media_file.catbox_url:
                # Fallback to URL if no local file
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='HTML'
                )
            else:
                return None

            return message.message_id

        except Exception as e:
            print(f"Channel upload error: {e}")
            return None

    @staticmethod
    async def update_channel_message(character: Character, context: ContextTypes.DEFAULT_TYPE,
                                      old_message_id: Optional[int]) -> Optional[int]:
        """Update existing channel message - FIXED TO SEND AS PHOTO"""
        try:
            if old_message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=old_message_id
                    )
                except:
                    pass

            return await TelegramUploader.upload_to_channel(character, context)

        except Exception as e:
            print(f"Channel update error: {e}")
            return None


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Factory for creating Character objects with validation"""

    @staticmethod
    def format_name(text: str) -> str:
        """Format character/anime names properly"""
        return ' '.join(word.capitalize() for word in text.split())

    @staticmethod
    async def create_from_upload(
            char_id: str,
            name: str,
            anime: str,
            rarity: int,
            media_file: MediaFile,
            uploader_id: int,
            uploader_name: str
    ) -> Character:
        """Create character with formatted data"""
        from datetime import datetime

        return Character(
            character_id=char_id,
            name=CharacterFactory.format_name(name),
            anime=CharacterFactory.format_name(anime),
            rarity=rarity,
            media_file=media_file,
            uploader_id=uploader_id,
            uploader_name=uploader_name,
            created_at=datetime.utcnow().isoformat()
        )


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command with parallel processing"""

    @staticmethod
    def format_upload_help() -> str:
        """Format upload command help message"""
        rarities = RarityLevel.get_all()
        rarity_list = '\n'.join([f"{level}. {name}" for level, name in rarities.items()])

        return (
            "📤 ᴜᴘʟᴏᴀᴅ ᴄᴏᴍᴍᴀɴᴅ ᴜꜱᴀɢᴇ:\n\n"
            "ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴀɴᴅ ᴜꜱᴇ:\n"
            "/upload ɪᴅ ɴᴀᴍᴇ ᴀɴɪᴍᴇ ʀᴀʀɪᴛʏ\n\n"
            "ᴇxᴀᴍᴘʟᴇ:\n"
            "/upload 69 ɴᴇᴢᴜᴋᴏ ᴅᴇᴍᴏɴꜱʟᴀʏᴇʀ 5\n\n"
            f"ᴀᴠᴀɪʟᴀʙʟᴇ ʀᴀʀɪᴛɪᴇꜱ:\n{rarity_list}"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /upload command with parallel processing"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args or len(context.args) != 4:
            await update.message.reply_text(UploadHandler.format_upload_help())
            return

        if not update.message.reply_to_message:
            await update.message.reply_text('❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ!')
            return

        char_id, name, anime, rarity_str = context.args

        # Validate rarity
        try:
            rarity_num = int(rarity_str)
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                await update.message.reply_text(
                    f'❌ Invalid rarity! Use a number between 1 and 15.\n\n'
                    f'Available rarities:\n{chr(10).join([f"{r.level}. {r.display_name}" for r in RarityLevel])}'
                )
                return
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number!')
            return

        # Check for duplicate ID
        existing = await collection.find_one({'id': char_id})
        if existing:
            await update.message.reply_text(f'❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ {char_id} ᴀʟʀᴇᴀᴅʏ ᴇxɪꜱᴛꜱ!')
            return

        processing_msg = await update.message.reply_text("🔄 **Processing upload...**")

        try:
            # Extract media
            media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)

            if not media_file:
                await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                return

            if not media_file.is_valid_image:
                await processing_msg.edit_text("❌ Only image files are allowed!")
                media_file.cleanup()
                return

            if not media_file.is_valid_size:
                await processing_msg.edit_text(f"❌ File too large! Maximum size: {BotConfig.MAX_FILE_SIZE / (1024 * 1024):.1f} MB")
                media_file.cleanup()
                return

            # Check for duplicate hash
            duplicate = await collection.find_one({'file_hash': media_file.hash})
            if duplicate:
                await processing_msg.edit_text(
                    f'❌ This image is already uploaded!\n\n'
                    f'Character: {duplicate["name"]}\n'
                    f'ID: {duplicate["id"]}'
                )
                media_file.cleanup()
                return

            # Create character
            character = await CharacterFactory.create_from_upload(
                char_id, name, anime, rarity_num,
                media_file,
                update.effective_user.id,
                update.effective_user.first_name
            )

            await processing_msg.edit_text("🔄 **Uploading to Catbox and Channel...**")

            # Parallel upload to Catbox and Telegram
            catbox_url, message_id = await asyncio.gather(
                CatboxUploader.upload(media_file.file_path, media_file.filename),
                TelegramUploader.upload_to_channel(character, context)
            )

            if not catbox_url:
                await processing_msg.edit_text("❌ Failed to upload to Catbox.")
                media_file.cleanup()
                return

            if not message_id:
                await processing_msg.edit_text("❌ Failed to post to channel.")
                media_file.cleanup()
                return

            # Update character with URLs and message ID
            character.media_file.catbox_url = catbox_url
            character.message_id = message_id

            # Save to database
            await collection.insert_one(character.to_dict())

            # Clean up
            media_file.cleanup()

            # CHANGE 1: Simple success message only
            await processing_msg.edit_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')

        except Exception as e:
            await update.message.reply_text(f'❌ Upload failed: {str(e)}')
            if 'media_file' in locals():
                media_file.cleanup()


# ===================== DELETE HANDLER =====================

class DeleteHandler:
    """Handles /delete command"""

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /delete command"""
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 ᴀꜱᴋ ᴍʏ ᴏᴡɴᴇʀ...')
            return

        if not context.args:
            await update.message.reply_text('❌ ᴜꜱᴀɢᴇ: /delete ɪᴅ')
            return

        char_id = context.args[0]

        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ.')
            return

        # Delete from database
        await collection.delete_one({'id': char_id})

        # Try to delete from channel
        try:
            if 'message_id' in character and character['message_id']:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
                await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')
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
                f'✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.'
            )


class UpdateHandler:
    """Handles /update command"""

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
        """Handle /update command with validation fixes"""
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

                try:
                    media_file = await MediaHandler.extract_from_reply(update.message.reply_to_message)

                    if not media_file or not media_file.is_valid_image:
                        await processing_msg.edit_text("❌ Invalid media! Only photos and image documents are allowed.")
                        return

                    # Create character for parallel upload
                    char_for_upload = Character(
                        character_id=character['id'],
                        name=character['name'],
                        anime=character['anime'],
                        rarity=character['rarity'],  # Already integer
                        media_file=media_file,
                        uploader_id=update.effective_user.id,
                        uploader_name=update.effective_user.first_name
                    )

                    # FIXED: Use coroutines directly with asyncio.gather
                    await processing_msg.edit_text("🔄 **Uploading new image and updating channel...**")

                    # Run both operations concurrently
                    catbox_url, new_message_id = await asyncio.gather(
                        CatboxUploader.upload(media_file.file_path, media_file.filename),
                        TelegramUploader.update_channel_message(
                            char_for_upload, 
                            context, 
                            character.get('message_id')
                        )
                    )

                    if not catbox_url:
                        await processing_msg.edit_text("❌ Failed to upload to Catbox.")
                        media_file.cleanup()
                        return

                    update_data['img_url'] = catbox_url
                    update_data['file_hash'] = media_file.hash
                    update_data['message_id'] = new_message_id

                    media_file.cleanup()
                    await processing_msg.edit_text('✅ ɪᴍᴀɢᴇ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')

                except Exception as e:
                    await update.message.reply_text(f'❌ Failed to update image: {str(e)}')
                    return

            else:
                # Fix: Validate context.args length before accessing
                if len(context.args) < 3:
                    await update.message.reply_text('❌ Missing image URL. Usage: /update id img_url URL')
                    return

                new_value = context.args[2]
                update_data['img_url'] = new_value

        elif field in ['name', 'anime']:
            # Fix: Validate context.args length
            if len(context.args) < 3:
                await update.message.reply_text(
                    f'❌ Missing value. Usage: /update id {field} new_value'
                )
                return

            new_value = context.args[2]
            update_data[field] = CharacterFactory.format_name(new_value)

        elif field == 'rarity':
            # Fix: Validate context.args length
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
        from datetime import datetime
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
                # Create character object for channel update
                channel_char = Character(
                    character_id=updated_character['id'],
                    name=updated_character['name'],
                    anime=updated_character['anime'],
                    rarity=updated_character['rarity'],
                    media_file=MediaFile(catbox_url=updated_character['img_url']),
                    uploader_id=update.effective_user.id,
                    uploader_name=update.effective_user.first_name
                )

                await TelegramUploader.update_channel_message(
                    channel_char,
                    context,
                    updated_character['message_id']
                )
            except Exception:
                pass  # Channel update is optional

        await update.message.reply_text('✅ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴜᴘᴅᴀᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!')


# ===================== APPLICATION SETUP =====================

# Register command handlers with non-blocking option
application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    await SessionManager.close()
