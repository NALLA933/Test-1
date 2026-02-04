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


@dataclass(frozen=True)
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
                    limit_per_host=30,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True
                )
                cls._session = ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(
                        total=UploadConfig.DOWNLOAD_TIMEOUT,
                        connect=30,
                        sock_read=30
                    )
                )

        try:
            yield cls._session
        except Exception:
            raise

    @classmethod
    async def close(cls):
        if cls._session and not cls._session.closed:
            await cls._session.close()


class ProgressTracker:
    def __init__(self, message: Message):
        self.message = message
        self.last_update = 0
        self.update_interval = 2

    async def update(self, current: int, total: int, stage: str):
        import time
        current_time = time.time()

        if current_time - self.last_update < self.update_interval and current < total:
            return

        self.last_update = current_time

        if total > 0:
            percentage = (current / total) * 100
            progress_bar = self._create_progress_bar(percentage)
            size_mb = total / (1024 * 1024)

            try:
                await self.message.edit_text(
                    f'⏳ {stage}\n\n'
                    f'{progress_bar}\n'
                    f'{percentage:.1f}% • {size_mb:.2f} MB'
                )
            except Exception:
                pass

    @staticmethod
    def _create_progress_bar(percentage: float, length: int = 10) -> str:
        filled = int((percentage / 100) * length)
        bar = '▰' * filled + '▱' * (length - filled)
        return f'[{bar}]'


class TextFormatter:
    @staticmethod
    def format_name(name: str) -> str:
        return name.replace('-', ' ').title()

    @staticmethod
    def validate_hyphenated_format(value: str) -> bool:
        return bool(value and '-' in value and not value.startswith('-') and not value.endswith('-'))


class FileDownloader:
    @staticmethod
    async def download_with_progress(
        url: str,
        progress_callback=None
    ) -> Optional[bytes]:
        try:
            async with SessionManager.get_session() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None

                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    chunks = []

                    async for chunk in response.content.iter_chunked(UploadConfig.CHUNK_SIZE):
                        chunks.append(chunk)
                        downloaded += len(chunk)

                        if progress_callback:
                            await progress_callback(downloaded, total_size, 'Downloading')

                    return b''.join(chunks)

        except Exception:
            return None

    @staticmethod
    async def download_telegram_file(
        file,
        progress_callback=None
    ) -> Optional[bytes]:
        try:
            file_obj = await file.get_file()
            
            bio = io.BytesIO()
            await file_obj.download_to_memory(bio)
            
            file_bytes = bio.getvalue()
            
            if progress_callback:
                await progress_callback(len(file_bytes), len(file_bytes), 'Processing')
            
            return file_bytes

        except Exception as e:
            print(f"Error downloading Telegram file: {e}")
            return None


class MultiServiceUploader:
    @staticmethod
    async def upload_with_progress(
        file_bytes: bytes,
        filename: str,
        progress_callback=None
    ) -> Optional[str]:
        services = [
            MultiServiceUploader._upload_imgbb,
            MultiServiceUploader._upload_telegraph,
            MultiServiceUploader._upload_catbox
        ]

        for i, upload_func in enumerate(services, 1):
            try:
                if progress_callback:
                    await progress_callback(i, len(services), f'Uploading (Service {i}/{len(services)})')

                url = await upload_func(file_bytes, filename)
                if url:
                    return url

            except Exception:
                continue

        return None

    @staticmethod
    async def _upload_imgbb(file_bytes: bytes, filename: str) -> Optional[str]:
        try:
            async with SessionManager.get_session() as session:
                data = aiohttp.FormData()
                data.add_field('key', UploadConfig.IMGBB_API_KEY)
                data.add_field('image', base64.b64encode(file_bytes).decode())

                async with session.post(UploadConfig.IMGBB_API, data=data, timeout=aiohttp.ClientTimeout(total=UploadConfig.UPLOAD_TIMEOUT)) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get('data', {}).get('url')
        except Exception:
            pass
        return None

    @staticmethod
    async def _upload_telegraph(file_bytes: bytes, filename: str) -> Optional[str]:
        try:
            async with SessionManager.get_session() as session:
                data = aiohttp.FormData()
                data.add_field('file', file_bytes, filename=filename)

                async with session.post(UploadConfig.TELEGRAPH_API, data=data, timeout=aiohttp.ClientTimeout(total=UploadConfig.UPLOAD_TIMEOUT)) as response:
                    if response.status == 200:
                        result = await response.json()
                        if isinstance(result, list) and len(result) > 0:
                            return f"https://telegra.ph{result[0].get('src', '')}"
        except Exception:
            pass
        return None

    @staticmethod
    async def _upload_catbox(file_bytes: bytes, filename: str) -> Optional[str]:
        try:
            async with SessionManager.get_session() as session:
                data = aiohttp.FormData()
                data.add_field('reqtype', 'fileupload')
                data.add_field('fileToUpload', file_bytes, filename=filename)

                async with session.post(UploadConfig.CATBOX_API, data=data, timeout=aiohttp.ClientTimeout(total=UploadConfig.UPLOAD_TIMEOUT)) as response:
                    if response.status == 200:
                        url = await response.text()
                        return url.strip() if url else None
        except Exception:
            pass
        return None


class TelegramUploader:
    @staticmethod
    async def send_to_channel(
        character: Character,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Optional[Message]:
        try:
            caption = character.get_caption()

            message = await TelegramUploader._send_media_url(
                character.media_file.url,
                character.media_file.media_type,
                caption,
                context
            )

            return message

        except Exception:
            return None

    @staticmethod
    async def _send_media_url(
        url: str,
        media_type: MediaType,
        caption: str,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Message:
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


class DatabaseManager:
    @staticmethod
    async def save_character(character: Character) -> bool:
        try:
            char_dict = character.to_dict()

            result = await collection.insert_one(char_dict)

            return result.inserted_id is not None

        except Exception:
            return False

    @staticmethod
    async def character_exists(character_id: str) -> bool:
        result = await collection.find_one({'id': character_id})
        return result is not None

    @staticmethod
    async def get_next_id() -> str:
        counter = await db.counters.find_one_and_update(
            {'_id': 'character_id'},
            {'$inc': {'sequence_value': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return str(counter['sequence_value']).zfill(2)


class CharacterUploadHandler:
    @staticmethod
    async def handle_reply_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply_msg = update.message.reply_to_message
        
        if not reply_msg:
            await update.message.reply_text(
                '❌ Usage Error\n\n'
                'Please reply to an image/video with:\n'
                '<code>/upload Character-Name Anime-Name rarity-number</code>\n\n'
                'Example:\n'
                '<code>/upload Rimuru-Tempest That-Time-I-Got-Reincarnated-as-a-Slime 3</code>',
                parse_mode='HTML'
            )
            return

        if not any([reply_msg.photo, reply_msg.video, reply_msg.animation, reply_msg.document]):
            await update.message.reply_text(
                '❌ No media found!\n\n'
                'Please reply to a message containing an image, video, or animation.'
            )
            return

        args = context.args
        if len(args) < 3:
            await update.message.reply_text(
                '❌ Invalid Format\n\n'
                'Usage: <code>/upload Character-Name Anime-Name rarity-number</code>\n\n'
                'Example:\n'
                '<code>/upload Rimuru-Tempest That-Time-I-Got-Reincarnated-as-a-Slime 3</code>',
                parse_mode='HTML'
            )
            return

        rarity_num = args[-1]
        anime_name = args[-2]
        char_name = args[-3]

        if not TextFormatter.validate_hyphenated_format(char_name):
            await update.message.reply_text(
                '❌ Invalid character name format!\n\n'
                'Character name must be in hyphenated format.\n'
                'Example: Rimuru-Tempest\n\n'
                'Usage: <code>/upload Character-Name Anime-Name rarity-number</code>',
                parse_mode='HTML'
            )
            return

        if not TextFormatter.validate_hyphenated_format(anime_name):
            await update.message.reply_text(
                '❌ Invalid anime name format!\n\n'
                'Anime name must be in hyphenated format.\n'
                'Example: That-Time-I-Got-Reincarnated-as-a-Slime\n\n'
                'Usage: <code>/upload Character-Name Anime-Name rarity-number</code>',
                parse_mode='HTML'
            )
            return

        try:
            rarity_int = int(rarity_num)
            rarity = RarityLevel.from_number(rarity_int)
            if not rarity:
                await update.message.reply_text(
                    f'❌ Invalid rarity: {rarity_num}\n\n'
                    'Valid rarities are 1-15:\n'
                    '1: Common, 2: Rare, 3: Legendary, 4: Special,\n'
                    '5: Ancient, 6: Celestial, 7: Epic, 8: Cosmic,\n'
                    '9: Nightmare, 10: Frostborn, 11: Valentine,\n'
                    '12: Spring, 13: Tropical, 14: Kawaii, 15: Hybrid'
                )
                return
        except ValueError:
            await update.message.reply_text(f'❌ Rarity must be a number (1-15), got: {rarity_num}')
            return

        processing_msg = await update.message.reply_text('⏳ Processing your upload...')

        try:
            media_file_obj = None
            media_type = None
            
            if reply_msg.photo:
                media_file_obj = reply_msg.photo[-1]
                media_type = MediaType.IMAGE
            elif reply_msg.video:
                media_file_obj = reply_msg.video
                media_type = MediaType.VIDEO
            elif reply_msg.animation:
                media_file_obj = reply_msg.animation
                media_type = MediaType.ANIMATION
            elif reply_msg.document:
                media_file_obj = reply_msg.document
                media_type = MediaType.DOCUMENT

            await processing_msg.edit_text('⏳ Downloading media from Telegram...')
            progress = ProgressTracker(processing_msg)
            
            file_bytes = await FileDownloader.download_telegram_file(
                media_file_obj,
                progress.update
            )

            if not file_bytes:
                await processing_msg.edit_text('❌ Failed to download media from Telegram.')
                return

            temp_url = f"telegram_{hashlib.md5(file_bytes).hexdigest()[:8]}.jpg"
            media_file = MediaFile(
                url=temp_url,
                file_bytes=file_bytes,
                media_type=media_type
            )

            if not media_file.is_valid_size:
                await processing_msg.edit_text(
                    f'❌ File too large: {media_file.size / (1024*1024):.2f} MB\n'
                    f'Maximum allowed: {UploadConfig.MAX_FILE_SIZE / (1024*1024):.2f} MB'
                )
                return

            await processing_msg.edit_text('⏳ Uploading to ImgBB → Telegraph → Catbox...')

            uploaded_url = await MultiServiceUploader.upload_with_progress(
                file_bytes,
                media_file.filename,
                progress.update
            )

            if not uploaded_url:
                await processing_msg.edit_text(
                    '❌ Upload failed on all services.\n\n'
                    'Services tried: ImgBB, Telegraph, Catbox\n'
                    'Please try again later.'
                )
                return

            media_file = MediaFile(
                url=uploaded_url,
                file_bytes=file_bytes,
                media_type=media_type
            )

            formatted_name = TextFormatter.format_name(char_name)
            formatted_anime = TextFormatter.format_name(anime_name)
            character_id = await DatabaseManager.get_next_id()

            character = Character(
                character_id=character_id,
                name=formatted_name,
                anime=formatted_anime,
                rarity=rarity,
                media_file=media_file,
                uploader_id=str(update.effective_user.id),
                uploader_name=update.effective_user.first_name
            )

            await processing_msg.edit_text('⏳ Sending to channel...')

            channel_msg = await TelegramUploader.send_to_channel(character, context)

            if not channel_msg:
                await processing_msg.edit_text('❌ Failed to send to channel.')
                return

            character.message_id = channel_msg.message_id

            if channel_msg.video:
                character.file_id = channel_msg.video.file_id
                character.file_unique_id = channel_msg.video.file_unique_id
            elif channel_msg.photo:
                character.file_id = channel_msg.photo[-1].file_id
                character.file_unique_id = channel_msg.photo[-1].file_unique_id
            elif channel_msg.animation:
                character.file_id = channel_msg.animation.file_id
                character.file_unique_id = channel_msg.animation.file_unique_id
            elif channel_msg.document:
                character.file_id = channel_msg.document.file_id
                character.file_unique_id = channel_msg.document.file_unique_id

            if await DatabaseManager.save_character(character):
                await processing_msg.edit_text(
                    f'✅ Character uploaded successfully!\n\n'
                    f'🆔 ID: {character_id}\n'
                    f'👤 Name: {formatted_name}\n'
                    f'📺 Anime: {formatted_anime}\n'
                    f'⭐ Rarity: {rarity.display_name}\n'
                    f'🔗 URL: {uploaded_url[:50]}...'
                )
            else:
                await processing_msg.edit_text('❌ Failed to save to database.')

        except Exception as e:
            await processing_msg.edit_text(
                f'❌ Upload failed!\n\n'
                f'Error: {type(e).__name__}\n'
                f'Details: {str(e)}'
            )

    @staticmethod
    async def handle_url_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            '❌ URL upload is deprecated!\n\n'
            'Please use the new reply-based upload:\n'
            '1. Send/forward the image/video\n'
            '2. Reply to it with:\n'
            '<code>/upload Character-Name Anime-Name rarity-number</code>',
            parse_mode='HTML'
        )


class CharacterDeletionHandler:
    @staticmethod
    async def delete_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) != 1:
            await update.message.reply_text(
                '❌ Usage: /delete <character_id>\n\n'
                'Example: /delete 01'
            )
            return

        char_id = context.args[0]

        processing_msg = await update.message.reply_text(f'⏳ Deleting character {char_id}...')

        character_data = await collection.find_one({'id': char_id})

        if not character_data:
            await processing_msg.edit_text(f'❌ Character {char_id} not found in database.')
            return

        try:
            if character_data.get('message_id'):
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character_data['message_id']
                    )
                except Exception:
                    pass

            result = await collection.delete_one({'id': char_id})

            if result.deleted_count > 0:
                await processing_msg.edit_text(
                    f'✅ Character deleted successfully!\n\n'
                    f'🆔 ID: {char_id}\n'
                    f'👤 Name: {character_data.get("name", "Unknown")}\n'
                    f'📺 Anime: {character_data.get("anime", "Unknown")}'
                )
            else:
                await processing_msg.edit_text(f'❌ Failed to delete character {char_id} from database.')

        except Exception as e:
            await processing_msg.edit_text(
                f'❌ Deletion failed: {type(e).__name__}\n{str(e)}'
            )


class CharacterUpdateHandler:
    @staticmethod
    async def update_character(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 3:
            await update.message.reply_text(
                '❌ Usage: /update <character_id> <field> <new_value>\n\n'
                'Fields: name, anime, rarity, img_url\n\n'
                'Examples:\n'
                '/update 01 name Rimuru-Tempest\n'
                '/update 01 anime That-Time-I-Got-Reincarnated-as-a-Slime\n'
                '/update 01 rarity 5\n'
                '/update 01 img_url https://...'
            )
            return

        char_id = context.args[0]
        field = context.args[1].lower()
        
        valid_fields = ['name', 'anime', 'rarity', 'img_url']
        if field not in valid_fields:
            await update.message.reply_text(
                f'❌ Invalid field: {field}\n\n'
                f'Valid fields: {", ".join(valid_fields)}'
            )
            return

        new_value = context.args[2]

        character_data = await collection.find_one({'id': char_id})

        if not character_data:
            await update.message.reply_text(f'❌ Character {char_id} not found.')
            return

        processing_msg = await update.message.reply_text(
            f'⏳ Updating {field} for {char_id}...'
        )

        try:
            update_data = await CharacterUpdateHandler._process_field_update(
                field,
                new_value,
                processing_msg,
                update
            )

            if not update_data:
                return

            await collection.find_one_and_update(
                {'id': char_id},
                {'$set': update_data}
            )

            await CharacterUpdateHandler._update_channel_message(
                char_id,
                field,
                context,
                update.effective_user,
                processing_msg
            )

        except Exception as e:
            await processing_msg.edit_text(
                f'❌ Update failed: {type(e).__name__}\n{str(e)}'
            )

    @staticmethod
    async def _process_field_update(
        field: str,
        new_value: str,
        processing_msg: Message,
        update: Update
    ) -> Optional[Dict[str, Any]]:
        if field == 'name':
            if not TextFormatter.validate_hyphenated_format(new_value):
                await processing_msg.edit_text(
                    '❌ Invalid name format!\n\n'
                    'Name must be in hyphenated format.\n'
                    'Example: Rimuru-Tempest'
                )
                return None
            return {field: TextFormatter.format_name(new_value)}

        elif field == 'anime':
            if not TextFormatter.validate_hyphenated_format(new_value):
                await processing_msg.edit_text(
                    '❌ Invalid anime name format!\n\n'
                    'Anime name must be in hyphenated format.\n'
                    'Example: That-Time-I-Got-Reincarnated-as-a-Slime'
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

            if not media_file.is_valid_size:
                await processing_msg.edit_text('❌ File size exceeds limit.')
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