import io
import asyncio
import hashlib
import base64
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Tuple, Dict, List, Union, Any
from pathlib import Path
from functools import wraps, lru_cache
from contextlib import asynccontextmanager
import mimetypes

import aiohttp
from aiohttp import ClientSession, TCPConnector
from pymongo import ReturnDocument
from telegram import Update, InputFile, Message
from telegram.ext import CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError, TimedOut
from motor.motor_asyncio import AsyncIOMotorCollection

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config as BotConfig

sudo_users = [str(user_id) for user_id in BotConfig.SUDO_USERS]
OWNER_ID = BotConfig.OWNER_ID


class MediaType(Enum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    ANIMATION = "animation"

    @classmethod
    def from_mime(cls, mime_type: str) -> 'MediaType':
        if not mime_type:
            return cls.IMAGE

        mime_lower = mime_type.lower()
        if mime_lower.startswith('video'):
            return cls.VIDEO
        elif mime_lower.startswith('image/gif'):
            return cls.ANIMATION
        elif mime_lower.startswith('image'):
            return cls.IMAGE
        return cls.DOCUMENT


class RarityLevel(Enum):
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

    @property
    def emoji(self) -> str:
        return self._display.split()[0]

    @classmethod
    @lru_cache(maxsize=32)
    def from_number(cls, num: int) -> Optional['RarityLevel']:
        for rarity in cls:
            if rarity.level == num:
                return rarity
        return None


# FIX 1: Changed UploadConfig from dataclass to a simple class with class-level constants
class UploadConfig:
    MAX_FILE_SIZE: int = 50 * 1024 * 1024
    DOWNLOAD_TIMEOUT: int = 300
    UPLOAD_TIMEOUT: int = 300
    CHUNK_SIZE: int = 65536
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0
    CONNECTION_LIMIT: int = 100
    CATBOX_API: str = "https://catbox.moe/user/api.php"
    TELEGRAPH_API: str = "https://telegra.ph/upload"
    IMGBB_API: str = "https://api.imgbb.com/1/upload"
    IMGBB_API_KEY: str = "6d52008ec9026912f9f50c8ca96a09c3"
    ALLOWED_EXTENSIONS: tuple = ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.avi', '.mov', '.mkv', '.webm')


@dataclass
class MediaFile:
    url: str
    file_bytes: Optional[bytes] = None
    media_type: MediaType = MediaType.IMAGE
    filename: str = field(default="")
    mime_type: Optional[str] = None
    size: int = 0
    hash: str = field(default="")

    def __post_init__(self):
        if not self.filename:
            object.__setattr__(self, 'filename', self._generate_filename())

        if not self.mime_type:
            object.__setattr__(self, 'mime_type', self._detect_mime_type())

        if self.file_bytes and not self.size:
            object.__setattr__(self, 'size', len(self.file_bytes))

        if self.file_bytes and not self.hash:
            object.__setattr__(self, 'hash', self._compute_hash())

        if self.media_type == MediaType.IMAGE and not self.mime_type:
            object.__setattr__(self, 'mime_type', 'image/jpeg')

    def _generate_filename(self) -> str:
        ext = self._extract_extension()
        hash_part = hashlib.md5(self.url.encode()).hexdigest()[:8]
        return f"character_{hash_part}{ext}"

    def _extract_extension(self) -> str:
        url_lower = self.url.lower()

        video_exts = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv'}
        for ext in video_exts:
            if url_lower.endswith(ext):
                object.__setattr__(self, 'media_type', MediaType.VIDEO)
                return ext

        if url_lower.endswith('.gif'):
            object.__setattr__(self, 'media_type', MediaType.ANIMATION)
            return '.gif'

        image_exts = {'.jpg', '.jpeg', '.png', '.webp'}
        for ext in image_exts:
            if url_lower.endswith(ext):
                return ext

        return '.jpg'

    def _detect_mime_type(self) -> str:
        mime, _ = mimetypes.guess_type(self.filename)
        return mime or 'application/octet-stream'

    def _compute_hash(self) -> str:
        return hashlib.sha256(self.file_bytes).hexdigest()

    @property
    def is_video(self) -> bool:
        return self.media_type == MediaType.VIDEO

    @property
    def is_valid_size(self) -> bool:
        return self.size <= UploadConfig.MAX_FILE_SIZE


@dataclass
class Character:
    character_id: str
    name: str
    anime: str
    rarity: RarityLevel
    media_file: MediaFile
    uploader_id: str
    uploader_name: str
    message_id: Optional[int] = None
    file_id: Optional[str] = None
    file_unique_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        # FIX 4: Store rarity as display_name string consistently
        return {
            'id': self.character_id,
            'name': self.name,
            'anime': self.anime,
            'rarity': self.rarity.display_name,
            'img_url': self.media_file.url,
            'is_video': self.media_file.is_video,
            'message_id': self.message_id,
            'file_id': self.file_id,
            'file_unique_id': self.file_unique_id,
            'media_type': self.media_file.media_type.value,
            'file_hash': self.media_file.hash,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

    def get_caption(self, is_update: bool = False) -> str:
        media_type = {
            MediaType.VIDEO: "🎥 Video",
            MediaType.IMAGE: "🖼 Image",
            MediaType.ANIMATION: "🎬 Animation",
            MediaType.DOCUMENT: "📄 Document"
        }.get(self.media_file.media_type, "🖼 Image")

        action = "𝑼𝒑𝒅𝒂𝒕𝒆𝒅" if is_update else "𝑴𝒂𝒅𝒆"

        return (
            f'<b>{self.character_id}:</b> {self.name}\n'
            f'<b>{self.anime}</b>\n'
            f'<b>{self.rarity.emoji} 𝙍𝘼𝙍𝙄𝙏𝙔:</b> {self.rarity.display_name[2:]}\n'
            f'<b>Type:</b> {media_type}\n\n'
            f'{action} 𝑩𝒚 ➥ <a href="tg://user?id={self.uploader_id}">{self.uploader_name}</a>'
        )


@dataclass
class UploadResult:
    success: bool
    message: str
    character_id: Optional[str] = None
    character: Optional[Character] = None
    error: Optional[Exception] = None
    retry_count: int = 0


class SessionManager:
    _session: Optional[ClientSession] = None
    _lock = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def get_session(cls):
        async with cls._lock:
            if cls._session is None or cls._session.closed:
                connector = TCPConnector(
                    limit=UploadConfig.CONNECTION_LIMIT,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True
                )
                timeout = aiohttp.ClientTimeout(
                    total=UploadConfig.UPLOAD_TIMEOUT,
                    connect=60,
                    sock_read=UploadConfig.DOWNLOAD_TIMEOUT
                )
                cls._session = ClientSession(
                    connector=connector,
                    timeout=timeout,
                    raise_for_status=False
                )

        try:
            yield cls._session
        except Exception:
            raise

    @classmethod
    async def close_session(cls):
        async with cls._lock:
            if cls._session and not cls._session.closed:
                await cls._session.close()
                cls._session = None


class TextFormatter:
    @staticmethod
    def format_name(text: str) -> str:
        # FIX 5: Improved name validation - stricter hyphen rules
        if not text:
            return text
        
        # Replace spaces with hyphens
        text = text.strip().replace(' ', '-')
        
        # Remove multiple consecutive hyphens
        while '--' in text:
            text = text.replace('--', '-')
        
        # Remove leading/trailing hyphens
        text = text.strip('-')
        
        # Validate that hyphenated parts are non-empty
        parts = text.split('-')
        if not all(part for part in parts):
            return text
        
        # Title case each part
        return '-'.join(word.capitalize() for word in parts)

    @staticmethod
    def validate_hyphenated_name(text: str) -> bool:
        # FIX 5: Stronger validation for hyphenated names
        if not text:
            return False
        
        # Check for invalid starting/ending characters
        if text.startswith('-') or text.endswith('-'):
            return False
        
        # Check for consecutive hyphens
        if '--' in text:
            return False
        
        # Check that all parts between hyphens are non-empty and alphanumeric
        parts = text.split('-')
        if len(parts) < 1:
            return False
        
        for part in parts:
            if not part:
                return False
            if not part.replace(' ', '').isalnum():
                return False
        
        return True


class ProgressTracker:
    def __init__(self, message: Message):
        self.message = message
        self.last_update = 0
        self.update_interval = 3

    async def update(self, current: int, total: int, status: str = "") -> None:
        # FIX 7: Simplified progress - only update if real progress data is available
        import time
        current_time = time.time()
        
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        if total > 0:
            percentage = (current / total) * 100
            progress_text = f'⏳ {status}\n📊 Progress: {percentage:.1f}%'
        else:
            progress_text = f'⏳ {status}'
        
        try:
            await self.message.edit_text(progress_text)
        except Exception:
            pass


class FileDownloader:
    @staticmethod
    async def download_with_progress(url: str, progress_callback=None) -> Optional[bytes]:
        async with SessionManager.get_session() as session:
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    total_size = int(response.headers.get('content-length', 0))
                    
                    # FIX 6: Enforce MAX_FILE_SIZE validation
                    if total_size > UploadConfig.MAX_FILE_SIZE:
                        return None

                    chunks = []
                    downloaded = 0

                    async for chunk in response.content.iter_chunked(UploadConfig.CHUNK_SIZE):
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        
                        # FIX 6: Check size during download
                        if downloaded > UploadConfig.MAX_FILE_SIZE:
                            return None

                        if progress_callback and total_size > 0:
                            await progress_callback(downloaded, total_size, "Downloading")

                    return b''.join(chunks)

            except asyncio.TimeoutError:
                return None
            except Exception:
                return None

    @staticmethod
    async def download_telegram_file(file_obj, context: ContextTypes.DEFAULT_TYPE) -> Optional[bytes]:
        try:
            file = await context.bot.get_file(file_obj.file_id)
            
            # FIX 6: Validate file size before download
            if file.file_size and file.file_size > UploadConfig.MAX_FILE_SIZE:
                return None
            
            file_bytes = await file.download_as_bytearray()
            
            # FIX 6: Validate actual downloaded size
            if len(file_bytes) > UploadConfig.MAX_FILE_SIZE:
                return None
            
            return bytes(file_bytes)
        except Exception:
            return None


class MultiServiceUploader:
    @staticmethod
    async def upload_with_progress(file_bytes: bytes, filename: str, progress_callback=None) -> Optional[str]:
        services = [
            ('ImgBB', MultiServiceUploader._upload_imgbb),
            ('Telegraph', MultiServiceUploader._upload_telegraph),
            ('Catbox', MultiServiceUploader._upload_catbox)
        ]

        for service_name, upload_func in services:
            if progress_callback:
                await progress_callback(0, 100, f"Trying {service_name}")

            try:
                url = await upload_func(file_bytes, filename)
                if url:
                    if progress_callback:
                        await progress_callback(100, 100, f"Uploaded to {service_name}")
                    return url
            except Exception:
                continue

        return None

    @staticmethod
    async def _upload_imgbb(file_bytes: bytes, filename: str) -> Optional[str]:
        async with SessionManager.get_session() as session:
            try:
                data = aiohttp.FormData()
                data.add_field('key', UploadConfig.IMGBB_API_KEY)
                data.add_field('image', base64.b64encode(file_bytes).decode('utf-8'))

                async with session.post(UploadConfig.IMGBB_API, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('success'):
                            return result['data']['url']
            except Exception:
                pass
        return None

    @staticmethod
    async def _upload_telegraph(file_bytes: bytes, filename: str) -> Optional[str]:
        async with SessionManager.get_session() as session:
            try:
                data = aiohttp.FormData()
                data.add_field('file', file_bytes, filename=filename)

                async with session.post(UploadConfig.TELEGRAPH_API, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if isinstance(result, list) and len(result) > 0:
                            return f"https://telegra.ph{result[0]['src']}"
            except Exception:
                pass
        return None

    @staticmethod
    async def _upload_catbox(file_bytes: bytes, filename: str) -> Optional[str]:
        async with SessionManager.get_session() as session:
            try:
                data = aiohttp.FormData()
                data.add_field('reqtype', 'fileupload')
                data.add_field('fileToUpload', file_bytes, filename=filename)

                async with session.post(UploadConfig.CATBOX_API, data=data) as response:
                    if response.status == 200:
                        url = await response.text()
                        if url and url.startswith('http'):
                            return url.strip()
            except Exception:
                pass
        return None


class TelegramUploader:
    @staticmethod
    async def _send_media_url(
        url: str,
        media_type: MediaType,
        caption: str,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Message:
        # FIX 2: Use the REAL uploaded URL, not fake telegram_xxxx.jpg
        if media_type == MediaType.VIDEO:
            return await context.bot.send_video(
                chat_id=CHARA_CHANNEL_ID,
                video=url,
                caption=caption,
                parse_mode='HTML'
            )
        elif media_type == MediaType.ANIMATION:
            return await context.bot.send_animation(
                chat_id=CHARA_CHANNEL_ID,
                animation=url,
                caption=caption,
                parse_mode='HTML'
            )
        elif media_type == MediaType.DOCUMENT:
            return await context.bot.send_document(
                chat_id=CHARA_CHANNEL_ID,
                document=url,
                caption=caption,
                parse_mode='HTML'
            )
        else:
            return await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=url,
                caption=caption,
                parse_mode='HTML'
            )

    @staticmethod
    async def upload_to_channel(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        try:
            # FIX 2: Send the actual uploaded URL to Telegram
            message = await TelegramUploader._send_media_url(
                character.media_file.url,
                character.media_file.media_type,
                character.get_caption(),
                context
            )

            file_id = None
            file_unique_id = None

            if message.video:
                file_id = message.video.file_id
                file_unique_id = message.video.file_unique_id
            elif message.photo:
                file_id = message.photo[-1].file_id
                file_unique_id = message.photo[-1].file_unique_id
            elif message.animation:
                file_id = message.animation.file_id
                file_unique_id = message.animation.file_unique_id
            elif message.document:
                file_id = message.document.file_id
                file_unique_id = message.document.file_unique_id

            return message.message_id, file_id, file_unique_id

        except Exception:
            return None, None, None


class DatabaseHandler:
    @staticmethod
    async def get_next_sequence_number() -> int:
        result = await db.sequences.find_one_and_update(
            {'_id': 'character_id'},
            {'$inc': {'sequence_value': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return result['sequence_value']

    @staticmethod
    async def save_character(character: Character) -> bool:
        try:
            char_dict = character.to_dict()
            await collection.insert_one(char_dict)
            return True
        except Exception:
            return False


class CharacterUploadHandler:
    @staticmethod
    async def handle_reply_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply_msg = update.message.reply_to_message

        if len(context.args) < 3:
            await update.message.reply_text(
                '❌ Invalid format!\n\n'
                'Usage:\n'
                '<code>/upload Character-Name Anime-Name rarity-number</code>\n\n'
                'Example:\n'
                '<code>/upload Rimuru-Tempest That-Time-I-Got-Reincarnated-as-a-Slime 3</code>',
                parse_mode='HTML'
            )
            return

        char_name = context.args[0]
        anime_name = context.args[1]

        # FIX 5: Validate names using improved validation
        if not TextFormatter.validate_hyphenated_name(char_name):
            await update.message.reply_text(
                '❌ Invalid character name format!\n\n'
                'Requirements:\n'
                '• Use hyphens (-) to separate words\n'
                '• No spaces allowed\n'
                '• No consecutive hyphens (--)\n'
                '• Cannot start or end with hyphen\n\n'
                'Example: <code>Rimuru-Tempest</code>',
                parse_mode='HTML'
            )
            return

        if not TextFormatter.validate_hyphenated_name(anime_name):
            await update.message.reply_text(
                '❌ Invalid anime name format!\n\n'
                'Requirements:\n'
                '• Use hyphens (-) to separate words\n'
                '• No spaces allowed\n'
                '• No consecutive hyphens (--)\n'
                '• Cannot start or end with hyphen\n\n'
                'Example: <code>That-Time-I-Got-Reincarnated-as-a-Slime</code>',
                parse_mode='HTML'
            )
            return

        try:
            rarity_num = int(context.args[2])
            rarity = RarityLevel.from_number(rarity_num)
            if not rarity:
                await update.message.reply_text('❌ Invalid rarity! Use 1-15.')
                return
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number (1-15).')
            return

        processing_msg = await update.message.reply_text('⏳ Processing upload...')

        file_obj = None
        if reply_msg.photo:
            file_obj = reply_msg.photo[-1]
            media_type = MediaType.IMAGE
        elif reply_msg.video:
            file_obj = reply_msg.video
            media_type = MediaType.VIDEO
        elif reply_msg.animation:
            file_obj = reply_msg.animation
            media_type = MediaType.ANIMATION
        elif reply_msg.document:
            file_obj = reply_msg.document
            media_type = MediaType.DOCUMENT
        else:
            await processing_msg.edit_text('❌ No valid media found in replied message.')
            return

        await processing_msg.edit_text('⏳ Downloading media from Telegram...')
        file_bytes = await FileDownloader.download_telegram_file(file_obj, context)

        if not file_bytes:
            await processing_msg.edit_text('❌ Failed to download media or file too large.')
            return

        media_file = MediaFile(
            url="",
            file_bytes=file_bytes,
            media_type=media_type
        )

        # FIX 6: Validate file size
        if not media_file.is_valid_size:
            await processing_msg.edit_text(
                f'❌ File size exceeds limit!\n\n'
                f'Max allowed: {UploadConfig.MAX_FILE_SIZE / (1024*1024):.1f} MB'
            )
            return

        await processing_msg.edit_text('⏳ Uploading to hosting (ImgBB → Telegraph → Catbox)...')

        progress = ProgressTracker(processing_msg)
        uploaded_url = await MultiServiceUploader.upload_with_progress(
            file_bytes,
            media_file.filename,
            progress.update
        )

        if not uploaded_url:
            await processing_msg.edit_text(
                '❌ Upload failed on all services.\n\n'
                'Tried: ImgBB, Telegraph, Catbox\n'
                'Please try again later.'
            )
            return

        media_file = MediaFile(
            url=uploaded_url,
            file_bytes=file_bytes,
            media_type=media_type
        )

        char_id = f"character_{await DatabaseHandler.get_next_sequence_number()}"

        character = Character(
            character_id=char_id,
            name=TextFormatter.format_name(char_name),
            anime=TextFormatter.format_name(anime_name),
            rarity=rarity,
            media_file=media_file,
            uploader_id=str(update.effective_user.id),
            uploader_name=update.effective_user.first_name
        )

        await processing_msg.edit_text('⏳ Posting to channel...')

        message_id, file_id, file_unique_id = await TelegramUploader.upload_to_channel(
            character,
            context
        )

        if not message_id:
            await processing_msg.edit_text('❌ Failed to post to channel.')
            return

        character.message_id = message_id
        character.file_id = file_id
        character.file_unique_id = file_unique_id

        await processing_msg.edit_text('⏳ Saving to database...')

        if await DatabaseHandler.save_character(character):
            await processing_msg.edit_text(
                f'✅ Character uploaded successfully!\n\n'
                f'🆔 ID: {char_id}\n'
                f'📝 Name: {character.name}\n'
                f'📺 Anime: {character.anime}\n'
                f'⭐ Rarity: {rarity.display_name}'
            )
        else:
            await processing_msg.edit_text('⚠️ Posted to channel but database save failed.')


class CharacterDeletionHandler:
    @staticmethod
    async def delete_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text(
                '❌ Usage: <code>/delete character_id</code>',
                parse_mode='HTML'
            )
            return

        char_id = context.args[0]
        processing_msg = await update.message.reply_text(f'⏳ Deleting {char_id}...')

        character = await collection.find_one({'id': char_id})

        if not character:
            await processing_msg.edit_text(f'❌ Character {char_id} not found.')
            return

        try:
            if character.get('message_id'):
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
        except Exception:
            pass

        result = await collection.delete_one({'id': char_id})

        if result.deleted_count > 0:
            await processing_msg.edit_text(
                f'✅ Character deleted successfully!\n\n'
                f'🆔 ID: {char_id}\n'
                f'📝 Name: {character.get("name", "Unknown")}'
            )
        else:
            await processing_msg.edit_text(f'❌ Failed to delete {char_id} from database.')


class CharacterUpdateHandler:
    @staticmethod
    async def update_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # FIX 3: Fixed multi-word value handling for /update command
        if len(context.args) < 3:
            await update.message.reply_text(
                '❌ Invalid format!\n\n'
                'Usage:\n'
                '<code>/update character_id field new_value</code>\n\n'
                'Fields: name, anime, rarity, img_url\n\n'
                'Examples:\n'
                '<code>/update character_123 name Zero-Two</code>\n'
                '<code>/update character_123 anime Darling-in-the-Franxx</code>\n'
                '<code>/update character_123 rarity 5</code>',
                parse_mode='HTML'
            )
            return

        char_id = context.args[0]
        field = context.args[1].lower()
        
        # FIX 3: Join all remaining args to capture multi-word values
        new_value = ' '.join(context.args[2:])

        allowed_fields = ['name', 'anime', 'rarity', 'img_url']
        if field not in allowed_fields:
            await update.message.reply_text(
                f'❌ Invalid field!\n\n'
                f'Allowed fields: {", ".join(allowed_fields)}'
            )
            return

        processing_msg = await update.message.reply_text(f'⏳ Updating {char_id}...')

        character = await collection.find_one({'id': char_id})
        if not character:
            await processing_msg.edit_text(f'❌ Character {char_id} not found.')
            return

        update_data = await CharacterUpdateHandler._prepare_update_data(
            field,
            new_value,
            processing_msg
        )

        if update_data is None:
            return

        result = await collection.find_one_and_update(
            {'id': char_id},
            {'$set': update_data},
            return_document=ReturnDocument.AFTER
        )

        if result:
            await CharacterUpdateHandler._update_channel_message(
                char_id,
                field,
                context,
                update.effective_user,
                processing_msg
            )
        else:
            await processing_msg.edit_text(f'❌ Failed to update {char_id}.')

    @staticmethod
    async def _prepare_update_data(
        field: str,
        new_value: str,
        processing_msg: Message
    ) -> Optional[Dict[str, Any]]:
        if field in ['name', 'anime']:
            # FIX 5: Apply improved validation
            if not TextFormatter.validate_hyphenated_name(new_value):
                await processing_msg.edit_text(
                    f'❌ Invalid {field} format!\n\n'
                    'Requirements:\n'
                    '• Use hyphens (-) to separate words\n'
                    '• No spaces allowed\n'
                    '• No consecutive hyphens (--)\n'
                    '• Cannot start or end with hyphen'
                )
                return None
            return {field: TextFormatter.format_name(new_value)}

        elif field == 'rarity':
            try:
                rarity_num = int(new_value)
                rarity = RarityLevel.from_number(rarity_num)
                if not rarity:
                    await processing_msg.edit_text('❌ Invalid rarity (1-15).')
                    return None
                # FIX 4: Store rarity as display_name string consistently
                return {field: rarity.display_name}
            except ValueError:
                await processing_msg.edit_text('❌ Rarity must be a number.')
                return None

        elif field == 'img_url':
            await processing_msg.edit_text('⏳ Downloading new media...')

            try:
                progress = ProgressTracker(processing_msg)
                file_bytes = await FileDownloader.download_with_progress(
                    new_value,
                    progress.update
                )
            except Exception as e:
                await processing_msg.edit_text(f'❌ Download failed: {type(e).__name__}')
                return None

            if not file_bytes:
                await processing_msg.edit_text('❌ Failed to download media.')
                return None

            media_file = MediaFile(url=new_value, file_bytes=file_bytes)

            # FIX 6: Validate file size
            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f'❌ File size exceeds limit!\n\n'
                    f'Max allowed: {UploadConfig.MAX_FILE_SIZE / (1024*1024):.1f} MB'
                )
                return None

            await processing_msg.edit_text('⏳ Uploading (ImgBB → Telegraph → Catbox)...')

            uploaded_url = await MultiServiceUploader.upload_with_progress(
                file_bytes,
                media_file.filename,
                progress.update
            )

            if not uploaded_url:
                await processing_msg.edit_text(
                    '❌ Upload failed on all services.\n\n'
                    '🚫 Character NOT updated.\n'
                    'Please try again later.'
                )
                return None

            await processing_msg.edit_text('✅ Re-uploaded successfully!')

            return {
                'img_url': uploaded_url,
                'is_video': media_file.is_video,
                'media_type': media_file.media_type.value,
                'file_hash': media_file.hash
            }

        return None

    @staticmethod
    async def _update_channel_message(
        char_id: str,
        field: str,
        context: ContextTypes.DEFAULT_TYPE,
        user,
        processing_msg: Message
    ) -> None:
        character_data = await collection.find_one({'id': char_id})

        if not character_data:
            return

        is_video_file = character_data.get('is_video', False)
        media_type = character_data.get('media_type', 'image')

        media_type_display = {
            'video': '🎥 Video',
            'image': '🖼 Image',
            'animation': '🎬 Animation',
            'document': '📄 Document'
        }.get(media_type, '🖼 Image')

        # FIX 4: Handle rarity consistently as string
        rarity_text = character_data['rarity']
        emoji = rarity_text.split()[0]

        caption = (
            f'<b>{character_data["id"]}:</b> {character_data["name"]}\n'
            f'<b>{character_data["anime"]}</b>\n'
            f'<b>{emoji} 𝙍𝘼𝙍𝙄𝙏𝙔:</b> {rarity_text[2:]}\n'
            f'<b>Type:</b> {media_type_display}\n\n'
            f'𝑼𝒑𝒅𝒂𝒕𝒆𝒅 𝑩𝒚 ➥ <a href="tg://user?id={user.id}">{user.first_name}</a>'
        )

        try:
            if field == 'img_url':
                await CharacterUpdateHandler._replace_channel_media(
                    character_data,
                    caption,
                    context,
                    char_id
                )
            else:
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character_data['message_id'],
                    caption=caption,
                    parse_mode='HTML'
                )

            await processing_msg.edit_text(
                f'✅ Character updated successfully!\n'
                f'🆔 ID: {char_id}\n'
                f'📝 Field: {field}'
            )

        except Exception as e:
            await processing_msg.edit_text(
                f'⚠️ Database updated but channel sync failed.\n'
                f'Error: {type(e).__name__}'
            )

    @staticmethod
    async def _replace_channel_media(
        character_data: Dict,
        caption: str,
        context: ContextTypes.DEFAULT_TYPE,
        char_id: str
    ) -> None:
        try:
            await context.bot.delete_message(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character_data['message_id']
            )
        except Exception:
            pass

        new_url = character_data['img_url']
        media_type_str = character_data.get('media_type', 'image')

        if media_type_str == 'video':
            media_type = MediaType.VIDEO
        elif media_type_str == 'animation':
            media_type = MediaType.ANIMATION
        elif media_type_str == 'document':
            media_type = MediaType.DOCUMENT
        else:
            media_type = MediaType.IMAGE

        # FIX 2: Use real URL for channel upload
        message = await TelegramUploader._send_media_url(
            new_url,
            media_type,
            caption,
            context
        )

        update_fields = {'message_id': message.message_id}

        if message.video:
            update_fields['file_id'] = message.video.file_id
            update_fields['file_unique_id'] = message.video.file_unique_id
        elif message.photo:
            update_fields['file_id'] = message.photo[-1].file_id
            update_fields['file_unique_id'] = message.photo[-1].file_unique_id
        elif message.animation:
            update_fields['file_id'] = message.animation.file_id
            update_fields['file_unique_id'] = message.animation.file_unique_id
        elif message.document:
            update_fields['file_id'] = message.document.file_id
            update_fields['file_unique_id'] = message.document.file_unique_id

        await collection.find_one_and_update(
            {'id': char_id},
            {'$set': update_fields}
        )


def require_sudo(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if user_id not in sudo_users:
            await update.message.reply_text(
                '❌ Access Denied\n\n'
                'This command requires sudo privileges.\n'
                f'Contact: {SUPPORT_CHAT}'
            )
            return
        return await func(update, context)
    return wrapper


@require_sudo
async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if update.message.reply_to_message:
            await CharacterUploadHandler.handle_reply_upload(update, context)
        else:
            await update.message.reply_text(
                '❌ Please reply to an image/video!\n\n'
                'Usage:\n'
                '1. Send or forward an image/video/animation\n'
                '2. Reply to it with:\n'
                '<code>/upload Character-Name Anime-Name rarity-number</code>\n\n'
                'Example:\n'
                '<code>/upload Rimuru-Tempest That-Time-I-Got-Reincarnated-as-a-Slime 3</code>',
                parse_mode='HTML'
            )
    except Exception as e:
        error_msg = (
            f'❌ Upload Failed\n\n'
            f'Error: {type(e).__name__}\n'
            f'Details: {str(e)}\n\n'
            f'Support: {SUPPORT_CHAT}'
        )
        await update.message.reply_text(error_msg)


@require_sudo
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await CharacterDeletionHandler.delete_character(update, context)
    except Exception as e:
        await update.message.reply_text(
            f'❌ Deletion failed: {type(e).__name__}\n{str(e)}'
        )


@require_sudo
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await CharacterUpdateHandler.update_character(update, context)
    except Exception as e:
        await update.message.reply_text(
            f'❌ Update failed: {type(e).__name__}\n{str(e)}'
        )


application.add_handler(CommandHandler('upload', upload_command, block=False))
application.add_handler(CommandHandler('delete', delete_command, block=False))
application.add_handler(CommandHandler('update', update_command, block=False))
