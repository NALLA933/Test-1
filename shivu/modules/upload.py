import asyncio
import hashlib
import io
import tempfile
import re
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple, Set
from functools import wraps
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timedelta
import time

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument, ASCENDING
from telegram import Update, InputFile, Message, PhotoSize, Document, InputMediaPhoto, InputMediaDocument
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config


# ===================== LOGGING SETUP =====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_upload.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ===================== RATE LIMITING =====================

class RateLimiter:
    """Token bucket rate limiter per user"""
    
    def __init__(self, max_uploads: int = 5, time_window: int = 3600):
        self.max_uploads = max_uploads
        self.time_window = time_window
        self.user_uploads: Dict[int, List[float]] = defaultdict(list)
        self.blocked_users: Set[int] = set()
    
    def check_limit(self, user_id: int) -> Tuple[bool, int]:
        """Check if user can upload. Returns (allowed, remaining_uploads)"""
        now = time.time()
        
        if user_id in self.blocked_users:
            return False, 0
        
        # Clean old timestamps
        self.user_uploads[user_id] = [
            ts for ts in self.user_uploads[user_id] 
            if now - ts < self.time_window
        ]
        
        current_uploads = len(self.user_uploads[user_id])
        
        if current_uploads >= self.max_uploads:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return False, 0
        
        return True, self.max_uploads - current_uploads
    
    def record_upload(self, user_id: int):
        """Record an upload timestamp"""
        self.user_uploads[user_id].append(time.time())
        logger.info(f"Upload recorded for user {user_id}")
    
    def block_user(self, user_id: int):
        """Block a user from uploading"""
        self.blocked_users.add(user_id)
        logger.warning(f"User {user_id} blocked")
    
    def unblock_user(self, user_id: int):
        """Unblock a user"""
        self.blocked_users.discard(user_id)
        logger.info(f"User {user_id} unblocked")


rate_limiter = RateLimiter(max_uploads=5, time_window=3600)


# ===================== INPUT VALIDATION =====================

class InputValidator:
    """Validates and sanitizes user inputs"""
    
    VALID_NAME_PATTERN = re.compile(r'^[\w\s\-\.\']+$', re.UNICODE)
    XSS_PATTERN = re.compile(r'[<>\"\'\\]')
    
    @staticmethod
    def sanitize_text(text: str, max_length: int = 100, allow_special: bool = False) -> str:
        """Sanitize text input"""
        if not text:
            raise ValueError("Text cannot be empty")
        
        text = text.strip()
        
        if len(text) > max_length:
            raise ValueError(f"Text too long (max {max_length} characters)")
        
        if len(text) < 2:
            raise ValueError("Text too short (minimum 2 characters)")
        
        if not allow_special:
            text = InputValidator.XSS_PATTERN.sub('', text)
        
        if not allow_special and not InputValidator.VALID_NAME_PATTERN.match(text):
            raise ValueError("Text contains invalid characters")
        
        return text
    
    @staticmethod
    def validate_rarity(rarity_str: str) -> int:
        """Validate rarity input"""
        try:
            rarity = int(rarity_str)
            if not 1 <= rarity <= 15:
                raise ValueError("Rarity must be between 1 and 15")
            return rarity
        except (ValueError, TypeError):
            raise ValueError(f"Invalid rarity value: {rarity_str}")
    
    @staticmethod
    def validate_url(url: str) -> str:
        """Validate URL format"""
        if not url or not url.startswith(('http://', 'https://')):
            raise ValueError("Invalid URL format")
        
        url = url.strip()
        if len(url) > 500:
            raise ValueError("URL too long")
        
        return url
    
    @staticmethod
    def validate_file_size(size: int, max_size: int = 20 * 1024 * 1024) -> bool:
        """Validate file size"""
        if size <= 0:
            raise ValueError("Invalid file size")
        if size > max_size:
            raise ValueError(f"File too large (max {max_size / 1024 / 1024:.1f}MB)")
        return True
    
    @staticmethod
    def validate_mime_type(mime_type: str, allowed_types: Tuple[str, ...]) -> bool:
        """Validate MIME type"""
        if not mime_type or mime_type not in allowed_types:
            raise ValueError(f"Invalid file type. Allowed: {', '.join(allowed_types)}")
        return True


# ===================== DATABASE SETUP =====================

async def setup_database_indexes():
    """Create database indexes for optimal performance"""
    try:
        await collection.create_index([("id", ASCENDING)], unique=True, background=True)
        await collection.create_index([("file_hash", ASCENDING)], background=True)
        await collection.create_index([("rarity", ASCENDING)], background=True)
        await collection.create_index([("uploader_id", ASCENDING)], background=True)
        await collection.create_index([
            ("name", ASCENDING),
            ("anime", ASCENDING)
        ], background=True)
        
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
        try:
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
        except Exception as e:
            logger.error(f"Error detecting media type: {e}")
        return None


class RarityLevel(Enum):
    """Rarity levels (1-15)"""
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
    RETRY_DELAY: float = 2.0
    CONNECTION_LIMIT: int = 50
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
            if os.path.exists(self.file_path):
                object.__setattr__(self, 'size', os.path.getsize(self.file_path))

    def _compute_hash(self) -> str:
        """Compute SHA256 hash efficiently"""
        sha256_hash = hashlib.sha256()
        try:
            if self.file_path:
                with open(self.file_path, "rb") as f:
                    for byte_block in iter(lambda: f.read(4096), b""):
                        sha256_hash.update(byte_block)
        except Exception as e:
            logger.error(f"Error computing hash: {e}")
        return sha256_hash.hexdigest()

    @property
    def is_valid_image(self) -> bool:
        if self.media_type in [MediaType.VIDEO, MediaType.ANIMATION]:
            return False
        if self.mime_type:
            return self.mime_type.startswith('image/')
        return self.media_type in [MediaType.PHOTO, MediaType.DOCUMENT]

    @property
    def is_valid_size(self) -> bool:
        return self.size <= BotConfig.MAX_FILE_SIZE

    def cleanup(self):
        """Clean up temporary file safely"""
        if self.file_path:
            try:
                import os
                if os.path.exists(self.file_path):
                    os.unlink(self.file_path)
                    logger.debug(f"Cleaned up: {self.file_path}")
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")


@dataclass
class Character:
    """Character entry"""
    character_id: str
    name: str
    anime: str
    rarity: int
    media_file: MediaFile
    uploader_id: int
    uploader_name: str
    message_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.character_id,
            'name': self.name,
            'anime': self.anime,
            'rarity': self.rarity,
            'img_url': self.media_file.catbox_url,
            'message_id': self.message_id,
            'uploader_id': self.uploader_id,
            'uploader_name': self.uploader_name,
            'file_hash': self.media_file.hash,
            'created_at': self.created_at or datetime.utcnow().isoformat(),
            'updated_at': self.updated_at or datetime.utcnow().isoformat()
        }

    def get_caption(self, action: str = "Added") -> str:
        rarity_obj = RarityLevel.from_number(self.rarity)
        display_name = rarity_obj.display_name if rarity_obj else f"Level {self.rarity}"
        
        return (
            f"<b>Character Name:</b> {self.name}\n"
            f"<b>Anime Name:</b> {self.anime}\n"
            f"<b>Rarity:</b> {display_name}\n"
            f"<b>{action} by:</b> {self.uploader_name}\n"
            f"<b>Character ID:</b> {self.character_id}"
        )


# ===================== SESSION MANAGER =====================

class SessionManager:
    """Manages aiohttp session lifecycle"""
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_session(cls) -> ClientSession:
        async with cls._lock:
            if cls._session is None or cls._session.closed:
                connector = TCPConnector(
                    limit=BotConfig.CONNECTION_LIMIT,
                    limit_per_host=30,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True
                )
                timeout = aiohttp.ClientTimeout(
                    total=BotConfig.DOWNLOAD_TIMEOUT,
                    connect=30,
                    sock_read=60
                )
                cls._session = ClientSession(
                    connector=connector,
                    timeout=timeout,
                    raise_for_status=False
                )
                logger.info("New session created")
        return cls._session

    @classmethod
    async def close(cls):
        async with cls._lock:
            if cls._session and not cls._session.closed:
                await cls._session.close()
                logger.info("Session closed")
                cls._session = None


# ===================== RETRY DECORATOR =====================

def retry_on_failure(max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Retry with exponential backoff"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (NetworkError, TimedOut, aiohttp.ClientError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                            f"Retry in {current_delay}s"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All attempts failed: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    raise
            
            raise last_exception if last_exception else Exception("Operation failed")
        
        return wrapper
    return decorator


# ===================== MEDIA HANDLER =====================

class MediaHandler:
    """Handles media extraction and download"""

    @staticmethod
    async def extract_from_reply(message: Message) -> Optional[MediaFile]:
        """Extract media from replied message"""
        try:
            media_type = MediaType.from_telegram_message(message)
            
            if not media_type:
                logger.warning("No valid media found")
                return None

            if media_type == MediaType.PHOTO:
                file_obj = message.photo[-1]
                filename = f"photo_{file_obj.file_unique_id}.jpg"
                mime_type = "image/jpeg"
            elif media_type == MediaType.DOCUMENT:
                file_obj = message.document
                filename = file_obj.file_name or f"doc_{file_obj.file_unique_id}"
                mime_type = file_obj.mime_type
            else:
                logger.warning(f"Unsupported media type: {media_type}")
                return None

            InputValidator.validate_file_size(file_obj.file_size)
            InputValidator.validate_mime_type(mime_type, BotConfig.ALLOWED_MIME_TYPES)

            file_path = await MediaHandler._download_file(file_obj, filename)
            
            if not file_path:
                return None

            media_file = MediaFile(
                file_path=file_path,
                media_type=media_type,
                filename=filename,
                mime_type=mime_type,
                size=file_obj.file_size,
                telegram_file_id=file_obj.file_id
            )

            logger.info(f"Media extracted: {filename}")
            return media_file

        except Exception as e:
            logger.error(f"Extract error: {e}", exc_info=True)
            return None

    @staticmethod
    @retry_on_failure(max_retries=3, delay=2.0)
    async def _download_file(file_obj, filename: str) -> Optional[str]:
        """Download file from Telegram"""
        try:
            temp_file = tempfile.NamedTemporaryFile(
                delete=False, 
                suffix=f"_{filename}",
                prefix="tg_"
            )
            temp_path = temp_file.name
            temp_file.close()

            file = await file_obj.get_file()
            await file.download_to_drive(temp_path)

            logger.info(f"Downloaded: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Download failed: {e}")
            try:
                import os
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except:
                pass
            raise


# ===================== CATBOX UPLOADER =====================

class CatboxUploader:
    """Handles file uploads to Catbox"""

    @staticmethod
    @retry_on_failure(max_retries=3, delay=2.0)
    async def upload(file_path: str, filename: str) -> Optional[str]:
        """Upload to Catbox"""
        try:
            session = await SessionManager.get_session()
            
            with open(file_path, 'rb') as f:
                file_data = f.read()

            form_data = aiohttp.FormData()
            form_data.add_field(
                'fileToUpload',
                file_data,
                filename=filename,
                content_type='application/octet-stream'
            )
            form_data.add_field('reqtype', 'fileupload')

            async with session.post(
                BotConfig.CATBOX_API,
                data=form_data,
                timeout=aiohttp.ClientTimeout(total=BotConfig.UPLOAD_TIMEOUT)
            ) as response:
                if response.status == 200:
                    url = await response.text()
                    url = url.strip()
                    
                    if url and url.startswith('http'):
                        logger.info(f"Catbox upload OK: {url}")
                        return url
                    else:
                        logger.error(f"Invalid response: {url}")
                        return None
                else:
                    error = await response.text()
                    logger.error(f"Upload failed: {response.status} - {error}")
                    return None

        except asyncio.TimeoutError:
            logger.error("Upload timeout")
            raise
        except Exception as e:
            logger.error(f"Upload error: {e}", exc_info=True)
            raise


# ===================== TELEGRAM UPLOADER =====================

class TelegramUploader:
    """Handles uploads to Telegram channel"""

    @staticmethod
    @retry_on_failure(max_retries=3, delay=1.0)
    async def send_to_channel(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Optional[int]:
        """Send to channel"""
        try:
            caption = character.get_caption("Added")
            
            sent_message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character.media_file.catbox_url,
                caption=caption,
                parse_mode='HTML'
            )
            
            logger.info(f"Sent to channel: {character.character_id}")
            return sent_message.message_id

        except BadRequest as e:
            logger.error(f"BadRequest: {e}")
            try:
                sent_message = await context.bot.send_document(
                    chat_id=CHARA_CHANNEL_ID,
                    document=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='HTML'
                )
                return sent_message.message_id
            except Exception as retry_error:
                logger.error(f"Send as doc failed: {retry_error}")
                raise
        except Exception as e:
            logger.error(f"Channel send error: {e}", exc_info=True)
            raise

    @staticmethod
    @retry_on_failure(max_retries=2, delay=1.0)
    async def update_channel_message(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE,
        message_id: Optional[int]
    ) -> Optional[int]:
        """Update channel message"""
        if not message_id:
            return await TelegramUploader.send_to_channel(character, context)

        try:
            caption = character.get_caption("Updated")
            
            await context.bot.edit_message_media(
                chat_id=CHARA_CHANNEL_ID,
                message_id=message_id,
                media=InputMediaPhoto(
                    media=character.media_file.catbox_url,
                    caption=caption,
                    parse_mode='HTML'
                )
            )
            
            logger.info(f"Message updated: {message_id}")
            return message_id

        except BadRequest as e:
            logger.warning(f"Edit failed, sending new: {e}")
            return await TelegramUploader.send_to_channel(character, context)
        except Exception as e:
            logger.error(f"Update error: {e}")
            raise


# ===================== CHARACTER FACTORY =====================

class CharacterFactory:
    """Creates and validates characters"""

    @staticmethod
    def format_name(name: str) -> str:
        """Format name"""
        try:
            name = InputValidator.sanitize_text(name, max_length=100)
            name = ' '.join(word.capitalize() for word in name.split())
            return name
        except ValueError as e:
            logger.error(f"Name validation failed: {e}")
            raise

    @staticmethod
    async def create(
        name: str,
        anime: str,
        rarity: int,
        media_file: MediaFile,
        uploader_id: int,
        uploader_name: str
    ) -> Character:
        """Create character"""
        try:
            formatted_name = CharacterFactory.format_name(name)
            formatted_anime = CharacterFactory.format_name(anime)
            
            rarity_obj = RarityLevel.from_number(rarity)
            if not rarity_obj:
                raise ValueError(f"Invalid rarity: {rarity}")

            character_id = await CharacterFactory._generate_unique_id()
            await CharacterFactory._check_duplicates(
                formatted_name,
                formatted_anime,
                media_file.hash
            )

            character = Character(
                character_id=character_id,
                name=formatted_name,
                anime=formatted_anime,
                rarity=rarity,
                media_file=media_file,
                uploader_id=uploader_id,
                uploader_name=uploader_name,
                created_at=datetime.utcnow().isoformat()
            )

            logger.info(f"Character created: {character_id}")
            return character

        except Exception as e:
            logger.error(f"Create error: {e}", exc_info=True)
            raise

    @staticmethod
    async def _generate_unique_id() -> str:
        """Generate unique ID"""
        last_character = await collection.find_one(
            {}, 
            sort=[("id", -1)],
            projection={"id": 1}
        )
        
        if last_character and 'id' in last_character:
            try:
                last_id = int(last_character['id'])
                new_id = str(last_id + 1)
            except (ValueError, TypeError):
                new_id = "1"
        else:
            new_id = "1"
        
        logger.debug(f"Generated ID: {new_id}")
        return new_id

    @staticmethod
    async def _check_duplicates(name: str, anime: str, file_hash: str):
        """Check for duplicates"""
        existing_file = await collection.find_one({'file_hash': file_hash})
        if existing_file:
            logger.warning(f"Duplicate file: {file_hash}")
            raise ValueError(
                f"⚠️ This image already exists!\n"
                f"Character: {existing_file.get('name', 'Unknown')}\n"
                f"ID: {existing_file.get('id', 'Unknown')}"
            )
        
        existing_char = await collection.find_one({
            'name': name,
            'anime': anime
        })
        if existing_char:
            logger.warning(f"Similar character: {name} from {anime}")
            raise ValueError(
                f"⚠️ Similar character exists!\n"
                f"Character: {name}\n"
                f"Anime: {anime}\n"
                f"ID: {existing_char.get('id', 'Unknown')}\n\n"
                f"Use /update to modify"
            )


# ===================== UPLOAD HANDLER =====================

class UploadHandler:
    """Handles /upload command"""

    @staticmethod
    def format_upload_help() -> str:
        rarity_list = "\n".join([
            f"{r.level}. {r.display_name}"
            for r in RarityLevel
        ])
        
        return (
            "📤 <b>Upload Command</b>\n\n"
            "<b>Usage:</b>\n"
            "1. Reply to photo/image\n"
            "2. Use: <code>/upload Name | Anime | Rarity</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/upload Nezuko | Demon Slayer | 5</code>\n\n"
            "<b>Rarity Levels:</b>\n"
            f"{rarity_list}\n\n"
            "<b>Rules:</b>\n"
            "• Max 20MB per file\n"
            "• 2-100 characters for names\n"
            "• 5 uploads per hour limit\n"
            "• No duplicates allowed"
        )

    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle upload command"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        if user_id not in Config.SUDO_USERS:
            await update.message.reply_text(
                '🔒 <b>Access Denied</b>\n\n'
                f'Contact: {SUPPORT_CHAT}',
                parse_mode='HTML'
            )
            logger.warning(f"Unauthorized: {user_id}")
            return

        allowed, remaining = rate_limiter.check_limit(user_id)
        if not allowed:
            await update.message.reply_text(
                '⏱️ <b>Rate Limit Exceeded</b>\n\n'
                'Max 5 uploads per hour',
                parse_mode='HTML'
            )
            logger.warning(f"Rate limit: {user_id}")
            return

        if not context.args:
            await update.message.reply_text(
                UploadHandler.format_upload_help(),
                parse_mode='HTML'
            )
            return

        if not update.message.reply_to_message:
            await update.message.reply_text(
                '❌ Reply to photo required',
                parse_mode='HTML'
            )
            return

        args_text = ' '.join(context.args)
        parts = [p.strip() for p in args_text.split('|')]

        if len(parts) != 3:
            await update.message.reply_text(
                '❌ <b>Invalid Format</b>\n\n'
                'Use: <code>/upload Name | Anime | Rarity</code>',
                parse_mode='HTML'
            )
            return

        name, anime, rarity_str = parts

        processing_msg = await update.message.reply_text(
            f'⏳ Processing... ({remaining - 1}/5 remaining)',
            parse_mode='HTML'
        )

        media_file = None
        try:
            # Validate inputs
            await processing_msg.edit_text('⏳ Step 1/5: Validating...')
            
            try:
                validated_name = InputValidator.sanitize_text(name, 100)
                validated_anime = InputValidator.sanitize_text(anime, 100)
                validated_rarity = InputValidator.validate_rarity(rarity_str)
            except ValueError as e:
                await processing_msg.edit_text(f'❌ <b>Error:</b>\n{e}', parse_mode='HTML')
                return

            # Extract media
            await processing_msg.edit_text('⏳ Step 2/5: Extracting media...')
            
            try:
                media_file = await MediaHandler.extract_from_reply(
                    update.message.reply_to_message
                )
            except ValueError as e:
                await processing_msg.edit_text(f'❌ <b>Media Error:</b>\n{e}', parse_mode='HTML')
                return

            if not media_file or not media_file.is_valid_image:
                await processing_msg.edit_text('❌ Invalid media (JPEG/PNG/WebP only)', parse_mode='HTML')
                return

            # Create character
            await processing_msg.edit_text('⏳ Step 3/5: Creating character...')
            
            try:
                character = await CharacterFactory.create(
                    name=validated_name,
                    anime=validated_anime,
                    rarity=validated_rarity,
                    media_file=media_file,
                    uploader_id=user_id,
                    uploader_name=user_name
                )
            except ValueError as e:
                await processing_msg.edit_text(f'❌ {e}', parse_mode='HTML')
                return

            # Upload
            await processing_msg.edit_text('⏳ Step 4/5: Uploading...')
            
            try:
                catbox_url, message_id = await asyncio.gather(
                    CatboxUploader.upload(media_file.file_path, media_file.filename),
                    TelegramUploader.send_to_channel(character, context)
                )
            except Exception as e:
                await processing_msg.edit_text(f'❌ Upload failed: {e}', parse_mode='HTML')
                return

            if not catbox_url:
                await processing_msg.edit_text('❌ Catbox upload failed', parse_mode='HTML')
                return

            character.media_file.catbox_url = catbox_url
            character.message_id = message_id

            # Save to DB
            await processing_msg.edit_text('⏳ Step 5/5: Saving...')
            
            try:
                await collection.insert_one(character.to_dict())
                logger.info(f"Saved: {character.character_id}")
            except Exception as e:
                await processing_msg.edit_text(f'❌ Database error: {e}', parse_mode='HTML')
                return

            rate_limiter.record_upload(user_id)

            rarity_display = RarityLevel.from_number(validated_rarity).display_name
            await processing_msg.edit_text(
                f'✅ <b>Upload Successful!</b>\n\n'
                f'<b>Name:</b> {character.name}\n'
                f'<b>Anime:</b> {character.anime}\n'
                f'<b>Rarity:</b> {rarity_display}\n'
                f'<b>ID:</b> {character.character_id}\n\n'
                f'Remaining: {remaining - 1}/5',
                parse_mode='HTML'
            )

        except Exception as e:
            await processing_msg.edit_text(
                f'❌ Unexpected error: {e}',
                parse_mode='HTML'
            )
            logger.error(f"Upload error: {e}", exc_info=True)

        finally:
            if media_file:
                media_file.cleanup()


# ===================== DELETE/UPDATE HANDLERS =====================
# (Keeping short for file size - same structure as before)

class DeleteHandler:
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user.id not in Config.SUDO_USERS:
            await update.message.reply_text('🔒 Access Denied')
            return

        if not context.args:
            await update.message.reply_text('/delete character_id')
            return

        char_id = context.args[0].strip()

        try:
            character = await collection.find_one({'id': char_id})
            
            if not character:
                await update.message.reply_text(f'❌ Not found: {char_id}')
                return

            result = await collection.delete_one({'id': char_id})
            
            if result.deleted_count > 0:
                if 'message_id' in character:
                    try:
                        await context.bot.delete_message(
                            CHARA_CHANNEL_ID,
                            character['message_id']
                        )
                    except:
                        pass

                await update.message.reply_text(f'✅ Deleted: {char_id}')
                logger.info(f"Deleted: {char_id}")

        except Exception as e:
            await update.message.reply_text(f'❌ Error: {e}')
            logger.error(f"Delete error: {e}")


class UpdateHandler:
    VALID_FIELDS = ['img_url', 'name', 'anime', 'rarity']
    
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user.id not in Config.SUDO_USERS:
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                '/update ID field value\nFields: img_url, name, anime, rarity'
            )
            return
        
        char_id = context.args[0]
        field = context.args[1]
        
        if field not in UpdateHandler.VALID_FIELDS:
            await update.message.reply_text('Invalid field')
            return
        
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('Character not found')
            return
        
        update_data = {}
        
        try:
            if field == 'name' or field == 'anime':
                if len(context.args) < 3:
                    return
                value = ' '.join(context.args[2:])
                update_data[field] = CharacterFactory.format_name(value)
            
            elif field == 'rarity':
                if len(context.args) < 3:
                    return
                update_data['rarity'] = InputValidator.validate_rarity(context.args[2])
            
            update_data['updated_at'] = datetime.utcnow().isoformat()
            
            await collection.update_one(
                {'id': char_id},
                {'$set': update_data}
            )
            
            await update.message.reply_text('✅ Updated successfully')
            logger.info(f"Updated {char_id}: {field}")

        except Exception as e:
            await update.message.reply_text(f'❌ Error: {e}')
            logger.error(f"Update error: {e}")


class StatsHandler:
    @staticmethod
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user.id not in Config.SUDO_USERS:
            return

        try:
            total = await collection.count_documents({})
            
            stats_msg = f"📊 <b>Statistics</b>\n\nTotal: {total}"
            
            await update.message.reply_text(stats_msg, parse_mode='HTML')

        except Exception as e:
            logger.error(f"Stats error: {e}")


# ===================== REGISTER HANDLERS =====================

application.add_handler(CommandHandler("upload", UploadHandler.handle, block=False))
application.add_handler(CommandHandler("delete", DeleteHandler.handle, block=False))
application.add_handler(CommandHandler("update", UpdateHandler.handle, block=False))
application.add_handler(CommandHandler("stats", StatsHandler.handle, block=False))


# ===================== CLEANUP =====================

async def cleanup():
    """Cleanup on shutdown"""
    logger.info("Cleanup started")
    await SessionManager.close()
    logger.info("Cleanup done")


async def startup():
    """Initialize on startup"""
    logger.info("Bot starting...")
    await setup_database_indexes()
    logger.info("Bot ready")