import io
import logging
import random
import asyncio
from enum import Enum
from functools import wraps
from datetime import datetime

from pymongo import ReturnDocument
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackContext

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

# ========== LOGGING SETUP ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== RARITY ENUM ==========
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

    @classmethod
    def get_by_number(cls, number):
        for rarity in cls:
            if rarity.value[0] == number:
                return rarity
        return None

# ========== TEXT MESSAGES ==========
WRONG_FORMAT_TEXT = """❌ Wrong format!

<b>Usage:</b> Reply to an image with:
<code>/upload character-name anime-name rarity-number</code>

<b>Example:</b>
<code>/upload naruto-uzumaki naruto 3</code>

<b>Available Rarities:</b>
1 - ⚪ ᴄᴏᴍᴍᴏɴ
2 - 🔵 ʀᴀʀᴇ
3 - 🟡 ʟᴇɢᴇɴᴅᴀʀʏ
4 - 💮 ꜱᴘᴇᴄɪᴀʟ
5 - 👹 ᴀɴᴄɪᴇɴᴛ
6 - 🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ
7 - 🔮 ᴇᴘɪᴄ
8 - 🪐 ᴄᴏꜱᴍɪᴄ
9 - ⚰️ ɴɪɢʜᴛᴍᴀʀᴇ
10 - 🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ
11 - 💝 ᴠᴀʟᴇɴᴛɪɴᴇ
12 - 🌸 ꜱᴘʀɪɴɢ
13 - 🏖️ ᴛʀᴏᴘɪᴄᴀʟ
14 - 🍭 ᴋᴀᴡᴀɪɪ
15 - 🧬 ʜʏʙʀɪᴅ"""

# ========== DECORATORS ==========
def admin_only(func):
    """Check if user is owner or sudo user"""
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = str(update.effective_user.id)
        if user_id != str(Config.OWNER_ID) and user_id not in [str(uid) for uid in Config.SUDO_USERS]:
            await update.message.reply_text('⛔ You do not have permission to use this command.')
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def log_command(func):
    """Log command usage"""
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user = update.effective_user
        command = update.message.text.split()[0] if update.message.text else "unknown"
        logger.info(f"Command {command} used by {user.id} ({user.username or user.first_name})")
        try:
            return await func(update, context, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {command}: {str(e)}", exc_info=True)
            raise
    return wrapper

# ========== IMAGE UPLOADER CLASS ==========
class ImageUploader:
    def __init__(self):
        self.services = [
            self._upload_to_imgbb,
            self._upload_to_telegraph,
            self._upload_to_catbox,
        ]
        # API Keys (hardcoded as per your request)
        self.imgbb_key = "6d52008ec9026912f9f50c8ca96a09c3"
        
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _upload_to_imgbb(self, image_data: bytes) -> str:
        """Upload to ImgBB with retry"""
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('image', io.BytesIO(image_data))
                data.add_field('key', self.imgbb_key)
                
                async with session.post(
                    "https://api.imgbb.com/1/upload", 
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('success'):
                            logger.info("ImgBB upload successful")
                            return result['data']['url']
                    elif response.status == 429:
                        logger.warning("ImgBB rate limited, will retry...")
                        raise Exception("Rate limited")
        except Exception as e:
            logger.warning(f"ImgBB attempt failed: {e}")
            raise
        return None

    async def _upload_to_telegraph(self, image_data: bytes) -> str:
        """Upload to Telegraph"""
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('file', io.BytesIO(image_data), filename='image.jpg')
                
                async with session.post(
                    "https://telegra.ph/upload",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if isinstance(result, list) and len(result) > 0:
                            logger.info("Telegraph upload successful")
                            return f"https://telegra.ph{result[0]['src']}"
        except Exception as e:
            logger.warning(f"Telegraph upload failed: {e}")
        return None

    async def _upload_to_catbox(self, image_data: bytes) -> str:
        """Upload to Catbox"""
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('reqtype', 'fileupload')
                data.add_field('fileToUpload', io.BytesIO(image_data), filename='image.jpg')
                
                async with session.post(
                    "https://catbox.moe/user/api.php",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        url = await response.text()
                        if url and url.startswith('http'):
                            logger.info("Catbox upload successful")
                            return url.strip()
        except Exception as e:
            logger.warning(f"Catbox upload failed: {e}")
        return None

    async def upload_with_failover(self, image_data: bytes) -> str:
        """Try multiple services until one succeeds"""
        # Shuffle for load balancing
        services = self.services.copy()
        random.shuffle(services)
        
        for service in services:
            try:
                url = await service(image_data)
                if url:
                    return url
            except Exception as e:
                logger.error(f"Service {service.__name__} failed: {e}")
                continue
        
        return None

# ========== DATABASE OPERATIONS ==========
async def get_next_sequence_number(sequence_name):
    """Get next ID with proper formatting"""
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    num = sequence_document['sequence_value']
    # Format: 001, 010, 100
    return f"{num:03d}"

# ========== COMMAND HANDLERS ==========
@admin_only
@log_command
async def upload(update: Update, context: CallbackContext) -> None:
    """Enhanced upload command with progress tracking"""
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            '❌ Please reply to an image with the upload command!\n\n' + WRONG_FORMAT_TEXT,
            parse_mode='HTML'
        )
        return

    if not update.message.reply_to_message.photo:
        await update.message.reply_text('❌ The replied message must contain an image!')
        return

    args = context.args
    if len(args) != 3:
        await update.message.reply_text(WRONG_FORMAT_TEXT, parse_mode='HTML')
        return

    # Progress message
    progress_msg = await update.message.reply_text('⏳ <b>Starting upload process...</b>', parse_mode='HTML')

    try:
        # Parse arguments
        character_name = args[0].replace('-', ' ').strip().title()
        anime_name = args[1].replace('-', ' ').strip().title()

        try:
            rarity_number = int(args[2])
        except ValueError:
            await progress_msg.edit_text('❌ Rarity must be a number between 1-15.')
            return

        rarity_level = RarityLevel.get_by_number(rarity_number)
        if not rarity_level:
            await progress_msg.edit_text(f'❌ Invalid rarity number.\n\n{WRONG_FORMAT_TEXT}', parse_mode='HTML')
            return

        rarity = rarity_level.value[1]

        # Step 1: Download image
        await progress_msg.edit_text('📥 <b>Downloading image...</b>', parse_mode='HTML')
        photo = update.message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        
        if len(image_bytes) > 10 * 1024 * 1024:  # 10MB check
            await progress_msg.edit_text('❌ Image too large! Max size: 10MB')
            return

        # Step 2: Upload to hosting
        await progress_msg.edit_text('☁️ <b>Uploading to cloud storage...</b>\n<i>This may take a few seconds...</i>', parse_mode='HTML')
        
        uploader = ImageUploader()
        img_url = await uploader.upload_with_failover(bytes(image_bytes))
        
        if not img_url:
            await progress_msg.edit_text('❌ Failed to upload image. All hosting services failed.\nPlease try again later.')
            return

        # Step 3: Generate ID and prepare data
        await progress_msg.edit_text('💾 <b>Saving to database...</b>', parse_mode='HTML')
        
        char_id = await get_next_sequence_number('character_id')
        
        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime_name,
            'rarity': rarity,
            'id': char_id,
            'created_at': datetime.utcnow(),
            'added_by': update.effective_user.id,
            'added_by_name': update.effective_user.first_name
        }

        # Step 4: Post to channel
        try:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=(
                    f'<b>🎴 Character:</b> {character_name}\n'
                    f'<b>📺 Anime:</b> {anime_name}\n'
                    f'<b>⭐ Rarity:</b> {rarity}\n'
                    f'<b>🆔 ID:</b> <code>{char_id}</code>\n\n'
                    f'<b>👤 Added by:</b> <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>\n'
                    f'<b>📅 Date:</b> {datetime.utcnow().strftime("%Y-%m-%d %H:%M")}'
                ),
                parse_mode='HTML',
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60,
                pool_timeout=60
            )
            character['message_id'] = message.message_id
            
        except Exception as e:
            logger.error(f"Channel post failed with URL: {e}")
            # Fallback: send image directly
            await progress_msg.edit_text('⚠️ <b>URL failed, sending image directly...</b>', parse_mode='HTML')
            
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=io.BytesIO(image_bytes),
                caption=(
                    f'<b>🎴 Character:</b> {character_name}\n'
                    f'<b>📺 Anime:</b> {anime_name}\n'
                    f'<b>⭐ Rarity:</b> {rarity}\n'
                    f'<b>🆔 ID:</b> <code>{char_id}</code>\n\n'
                    f'<b>👤 Added by:</b> <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>'
                ),
                parse_mode='HTML'
            )
            character['message_id'] = message.message_id

        # Step 5: Save to database
        await collection.insert_one(character)
        
        # Success message
        channel_username = str(CHARA_CHANNEL_ID)[4:] if str(CHARA_CHANNEL_ID).startswith('-100') else CHARA_CHANNEL_ID
        
        await progress_msg.delete()
        await update.message.reply_text(
            f'✅ <b>Character Added Successfully!</b>\n\n'
            f'🆔 ID: <code>{char_id}</code>\n'
            f'👤 Name: {character_name}\n'
            f'📺 Anime: {anime_name}\n'
            f'⭐ Rarity: {rarity}\n'
            f'🔗 <a href="{img_url}">Image Link</a>\n\n'
            f'<b>View in channel:</b> <a href="https://t.me/c/{channel_username}/{message.message_id}">Click here</a>',
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        logger.info(f"Character {char_id} ({character_name}) added successfully by {update.effective_user.id}")

    except Exception as e:
        logger.error(f"Upload failed: {str(e)}", exc_info=True)
        await progress_msg.edit_text(
            f'❌ <b>Upload Failed!</b>\n\n'
            f'Error: <code>{str(e)[:100]}</code>\n\n'
            f'If this persists, contact: {SUPPORT_CHAT}',
            parse_mode='HTML'
        )

@admin_only
@log_command
async def delete(update: Update, context: CallbackContext) -> None:
    """Enhanced delete command"""
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            '❌ <b>Incorrect format!</b>\n\n'
            '<b>Usage:</b> <code>/delete ID</code>\n'
            '<b>Example:</b> <code>/delete 042</code>',
            parse_mode='HTML'
        )
        return

    char_id = args[0]
    
    try:
        # Find character first
        character = await collection.find_one({'id': char_id})
        
        if not character:
            await update.message.reply_text(f'❌ Character with ID <code>{char_id}</code> not found.', parse_mode='HTML')
            return
        
        # Delete from channel if message exists
        if character.get('message_id'):
            try:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
                logger.info(f"Deleted message {character['message_id']} from channel")
            except Exception as e:
                logger.warning(f"Could not delete message from channel: {e}")
                # Continue anyway
        
        # Delete from database
        await collection.delete_one({'id': char_id})
        
        await update.message.reply_text(
            f'✅ <b>Character Deleted!</b>\n\n'
            f'🆔 ID: <code>{char_id}</code>\n'
            f'👤 Was: {character.get("name", "Unknown")}\n'
            f'📺 Anime: {character.get("anime", "Unknown")}',
            parse_mode='HTML'
        )
        logger.info(f"Character {char_id} deleted by {update.effective_user.id}")
        
    except Exception as e:
        logger.error(f"Delete failed: {e}", exc_info=True)
        await update.message.reply_text(f'❌ Error: {str(e)[:200]}')

@admin_only
@log_command
async def update(update: Update, context: CallbackContext) -> None:
    """Enhanced update command"""
    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            '❌ <b>Incorrect format!</b>\n\n'
            '<b>Usage:</b> <code>/update ID field new_value</code>\n\n'
            '<b>Fields:</b> name, anime, rarity, img_url\n\n'
            '<b>Examples:</b>\n'
            '<code>/update 042 name Naruto-Uzumaki</code>\n'
            '<code>/update 042 rarity 5</code>\n'
            '<code>/update 042 img_url https://example.com/image.jpg</code>',
            parse_mode='HTML'
        )
        return

    char_id, field, new_value = args[0], args[1], args[2]

    valid_fields = ['img_url', 'name', 'anime', 'rarity']
    if field not in valid_fields:
        await update.message.reply_text(
            f'❌ Invalid field. Use one of: <code>{", ".join(valid_fields)}</code>',
            parse_mode='HTML'
        )
        return

    try:
        # Find character
        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text(f'❌ Character with ID <code>{char_id}</code> not found.', parse_mode='HTML')
            return

        # Process new value
        if field in ['name', 'anime']:
            processed_value = new_value.replace('-', ' ').strip().title()
        elif field == 'rarity':
            try:
                rarity_num = int(new_value)
                rarity_level = RarityLevel.get_by_number(rarity_num)
                if not rarity_level:
                    raise ValueError
                processed_value = rarity_level.value[1]
            except ValueError:
                await update.message.reply_text('❌ Rarity must be a number between 1-15.')
                return
        else:
            processed_value = new_value

        # Update database
        await collection.update_one(
            {'id': char_id},
            {
                '$set': {
                    field: processed_value,
                    'updated_at': datetime.utcnow(),
                    'updated_by': update.effective_user.id
                }
            }
        )

        # Handle channel updates
        if field == 'img_url':
            # Delete old message and send new
            try:
                if character.get('message_id'):
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id']
                    )
            except Exception as e:
                logger.warning(f"Could not delete old message: {e}")

            new_msg = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=processed_value,
                caption=(
                    f'<b>🎴 Character:</b> {character["name"]}\n'
                    f'<b>📺 Anime:</b> {character["anime"]}\n'
                    f'<b>⭐ Rarity:</b> {character["rarity"]}\n'
                    f'<b>🆔 ID:</b> <code>{char_id}</code>\n\n'
                    f'<b>✏️ Updated by:</b> <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>\n'
                    f'<b>🔄 Field:</b> Image URL'
                ),
                parse_mode='HTML'
            )
            
            # Update message_id in DB
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': new_msg.message_id}}
            )
            
        else:
            # Edit caption only
            try:
                if character.get('message_id'):
                    await context.bot.edit_message_caption(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id'],
                        caption=(
                            f'<b>🎴 Character:</b> {character["name"] if field != "name" else processed_value}\n'
                            f'<b>📺 Anime:</b> {character["anime"] if field != "anime" else processed_value}\n'
                            f'<b>⭐ Rarity:</b> {character["rarity"] if field != "rarity" else processed_value}\n'
                            f'<b>🆔 ID:</b> <code>{char_id}</code>\n\n'
                            f'<b>✏️ Updated by:</b> <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>\n'
                            f'<b>🔄 Field:</b> {field}'
                        ),
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.warning(f"Could not edit caption: {e}")

        await update.message.reply_text(
            f'✅ <b>Updated Successfully!</b>\n\n'
            f'🆔 ID: <code>{char_id}</code>\n'
            f'🔄 Field: <code>{field}</code>\n'
            f'✨ New Value: <code>{processed_value[:50]}</code>',
            parse_mode='HTML'
        )
        logger.info(f"Character {char_id} updated by {update.effective_user.id}: {field} = {processed_value[:30]}")

    except Exception as e:
        logger.error(f"Update failed: {e}", exc_info=True)
        await update.message.reply_text(f'❌ Error: {str(e)[:200]}')

@admin_only
async def stats(update: Update, context: CallbackContext) -> None:
    """Show database statistics"""
    try:
        # Total count
        total = await collection.count_documents({})
        
        # Rarity distribution
        pipeline = [
            {'$group': {'_id': '$rarity', 'count': {'$sum': 1}}},
            {'$sort': {'count': -1}}
        ]
        rarity_stats = await collection.aggregate(pipeline).to_list(length=None)
        
        # Recent uploads (last 24 hours)
        from datetime import timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        recent = await collection.count_documents({'created_at': {'$gte': yesterday}})
        
        # Build message
        text = f"📊 <b>Database Statistics</b>\n\n"
        text += f"📦 <b>Total Characters:</b> <code>{total}</code>\n"
        text += f"📈 <b>Last 24h:</b> <code>+{recent}</code>\n\n"
        
        if rarity_stats:
            text += "<b>⭐ Rarity Distribution:</b>\n"
            for stat in rarity_stats:
                count = stat['count']
                percentage = (count / total) * 100 if total > 0 else 0
                bar = "█" * int(percentage / 5) + "░" * (20 - int(percentage / 5))
                rarity_name = stat['_id'] if stat['_id'] else "Unknown"
                text += f"{rarity_name}: <code>{count}</code> [{bar}] {percentage:.1f}%\n"
        
        await update.message.reply_text(text, parse_mode='HTML')
        logger.info(f"Stats viewed by {update.effective_user.id}")
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await update.message.reply_text(f'❌ Error fetching stats: {str(e)}')

# ========== HANDLERS ==========
application.add_handler(CommandHandler('upload', upload, block=False))
application.add_handler(CommandHandler('delete', delete, block=False))
application.add_handler(CommandHandler('update', update, block=False))
application.add_handler(CommandHandler('stats', stats, block=False))

logger.info("Admin module loaded successfully")
