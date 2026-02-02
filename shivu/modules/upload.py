import signal
import sys
import urllib.request
import logging
from typing import Optional, Tuple
from pymongo import ReturnDocument
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext, Application
from telegram.error import TelegramError, TimedOut, NetworkError

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

WRONG_FORMAT_TEXT = """Wrong ❌️ format...  eg. /upload Img_url muzan-kibutsuji Demon-slayer 3

img_url character-name anime-name rarity-number

use rarity number accordingly rarity Map

""" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])


class ShutdownHandler:
    def __init__(self, app: Application):
        self.app = app
        self.is_shutting_down = False

    def setup(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        if self.is_shutting_down:
            return
        
        self.is_shutting_down = True
        signal_name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
        logger.info(f"Received {signal_name}, initiating graceful shutdown...")
        
        try:
            logger.info("Stopping bot...")
            self.app.stop_running()
            
            logger.info("Closing database connection...")
            if db.client:
                db.client.close()
            
            logger.info("Shutdown complete.")
            sys.exit(0)
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            sys.exit(1)


def is_sudo(user_id: int) -> bool:
    try:
        return user_id == Config.OWNER_ID or user_id in Config.SUDO_USERS
    except Exception as e:
        logger.error(f"Error checking sudo status: {e}")
        return False


def validate_rarity(rarity_input: str) -> Tuple[Optional[Tuple[int, str]], Optional[str]]:
    try:
        rarity_num = int(rarity_input)
        if rarity_num not in RARITY_MAP:
            error_msg = "Invalid rarity! Use numbers 1-15.\n\n" + "\n".join(
                [f"{k}: {v[1]}" for k, v in RARITY_MAP.items()]
            )
            return None, error_msg
        return RARITY_MAP[rarity_num], None
    except ValueError:
        error_msg = "Rarity must be a number! Use 1-15.\n\n" + "\n".join(
            [f"{k}: {v[1]}" for k, v in RARITY_MAP.items()]
        )
        return None, error_msg


def validate_image_url(url: str) -> bool:
    if not url or not url.startswith(('http://', 'https://')):
        return False
    
    supported_domains = [
        'catbox.moe', 'files.catbox.moe',
        'telegra.ph', 'telegraph',
        'imgur.com', 'i.imgur.com',
        'postimg.cc', 'i.postimg.cc',
        'ibb.co', 'i.ibb.co',
        'imgbb.com', 'i.imgbb.com',
        'imageban.ru', 'picsum.photos',
        'unsplash.com', 'images.unsplash.com',
    ]
    
    url_lower = url.lower()
    is_supported = any(domain in url_lower for domain in supported_domains)
    
    if is_supported:
        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
        has_valid_ext = any(url_lower.endswith(ext) for ext in valid_extensions)
        is_telegraph = 'telegra.ph' in url_lower
        
        if has_valid_ext or is_telegraph:
            return True
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            content_type = response.headers.get('Content-Type', '').lower()
            return content_type.startswith('image/')
    except Exception as e:
        logger.warning(f"URL validation failed for {url}: {e}")
        return False


def build_caption(char_id: str, char_name: str, anime: str, rarity_display: str, 
                  uploader_id: int, uploader_name: str) -> str:
    emoji = rarity_display.split()[0]
    rarity_text = rarity_display.split()[1]
    
    return (
        f"{char_id}: {char_name}\n"
        f"{anime}\n"
        f"{emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n"
        f"Type: 🖼 Image\n"
        f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ 𝘼 𝙇 𝙀 𝙓"
    )


async def get_next_sequence_number(sequence_name: str) -> int:
    sequence_collection = db.sequences
    try:
        sequence_document = await sequence_collection.find_one_and_update(
            {'_id': sequence_name},
            {'$inc': {'sequence_value': 1}},
            return_document=ReturnDocument.AFTER,
            upsert=True
        )
        
        if not sequence_document:
            await sequence_collection.insert_one({'_id': sequence_name, 'sequence_value': 1})
            return 1
            
        return sequence_document['sequence_value']
    except Exception as e:
        logger.error(f"Sequence generation failed for {sequence_name}: {e}")
        raise


async def send_photo_to_channel(context: CallbackContext, img_url: str, 
                                caption: str) -> Optional[int]:
    try:
        message = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=img_url,
            caption=caption,
            parse_mode='HTML',
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30
        )
        return message.message_id
    except TimedOut:
        logger.error(f"Timeout sending photo to channel: {img_url}")
        raise
    except NetworkError as e:
        logger.error(f"Network error sending photo to channel: {e}")
        raise
    except TelegramError as e:
        logger.error(f"Telegram error sending photo to channel: {e}")
        raise


async def update_channel_media(context: CallbackContext, message_id: int, 
                               img_url: str, caption: str) -> bool:
    try:
        await context.bot.edit_message_media(
            chat_id=CHARA_CHANNEL_ID,
            message_id=message_id,
            media=InputMediaPhoto(media=img_url, caption=caption, parse_mode='HTML'),
            read_timeout=30,
            write_timeout=30
        )
        return True
    except TimedOut:
        logger.error(f"Timeout updating media for message {message_id}")
        return False
    except TelegramError as e:
        logger.error(f"Failed to update media for message {message_id}: {e}")
        return False


async def update_channel_caption(context: CallbackContext, message_id: int, 
                                 caption: str) -> bool:
    try:
        await context.bot.edit_message_caption(
            chat_id=CHARA_CHANNEL_ID,
            message_id=message_id,
            caption=caption,
            parse_mode='HTML',
            read_timeout=30,
            write_timeout=30
        )
        return True
    except TimedOut:
        logger.error(f"Timeout updating caption for message {message_id}")
        return False
    except TelegramError as e:
        logger.error(f"Failed to update caption for message {message_id}: {e}")
        return False


async def delete_channel_message(context: CallbackContext, message_id: int) -> bool:
    try:
        await context.bot.delete_message(
            chat_id=CHARA_CHANNEL_ID,
            message_id=message_id,
            read_timeout=20,
            write_timeout=20
        )
        return True
    except TelegramError as e:
        logger.warning(f"Failed to delete message {message_id} from channel: {e}")
        return False


async def upload(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not is_sudo(user_id):
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        args = context.args
        if len(args) != 4:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        img_url = args[0]
        character_name = args[1].replace('-', ' ').title()
        anime = args[2].replace('-', ' ').title()

        if not validate_image_url(img_url):
            await update.message.reply_text(
                '❌ Invalid Image URL.\n\n'
                'Supported: Catbox, Telegraph, Imgur, etc.\n'
                '❌ Telegram File IDs are not supported!'
            )
            return

        rarity_data, error_msg = validate_rarity(args[3])
        if error_msg:
            await update.message.reply_text(error_msg)
            return

        rarity_num, rarity_display = rarity_data
        char_id = str(await get_next_sequence_number('character_id')).zfill(2)

        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity_display,
            'id': char_id
        }

        caption = build_caption(
            char_id, character_name, anime, rarity_display,
            user_id, update.effective_user.first_name
        )

        message_id = None
        channel_upload_success = False
        
        try:
            message_id = await send_photo_to_channel(context, img_url, caption)
            character['message_id'] = message_id
            channel_upload_success = True
            logger.info(f"Character {char_id} uploaded to channel with message_id {message_id}")
        except Exception as channel_error:
            logger.error(f"Channel upload failed for character {char_id}: {channel_error}")

        try:
            await collection.insert_one(character)
            logger.info(f"Character {char_id} saved to database")
            
            if channel_upload_success:
                await update.message.reply_text('CHARACTER ADDED....')
            else:
                await update.message.reply_text(
                    "Character Added to Database but Channel upload failed. "
                    "Consider checking bot permissions or channel ID."
                )
                
        except Exception as db_error:
            logger.error(f"Database insert failed for character {char_id}: {db_error}")
            await update.message.reply_text(
                f'Character Upload Unsuccessful. Database Error.\n'
                f'Please contact support: {SUPPORT_CHAT}'
            )

    except Exception as e:
        logger.error(f"Upload command error (user {user_id}): {e}")
        await update.message.reply_text(
            f'Character Upload Unsuccessful. Error: {str(e)}\n'
            f'If you think this is a source error, forward to: {SUPPORT_CHAT}'
        )


async def delete(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not is_sudo(user_id):
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format... Please use: /delete ID')
            return

        char_id = args[0]
        character = await collection.find_one_and_delete({'id': char_id})

        if not character:
            await update.message.reply_text('Character not found.')
            return

        logger.info(f"Character {char_id} deleted from database")
        
        if 'message_id' in character:
            channel_deleted = await delete_channel_message(context, character['message_id'])
            if channel_deleted:
                await update.message.reply_text('DONE')
            else:
                await update.message.reply_text(
                    'Deleted from database, but channel message not found or already deleted.'
                )
        else:
            await update.message.reply_text('DONE')

    except Exception as e:
        logger.error(f"Delete command error (user {user_id}, char_id {args[0] if args else 'unknown'}): {e}")
        await update.message.reply_text(f'Error during deletion: {str(e)}')


async def update_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if not is_sudo(user_id):
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('Incorrect format. Please use: /update id field new_value')
            return

        char_id = args[0]
        field = args[1]
        raw_value = args[2]

        character = await collection.find_one({'id': char_id})
        if not character:
            await update.message.reply_text('Character not found.')
            return

        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if field not in valid_fields:
            await update.message.reply_text(
                f'Invalid field. Please use one of: {", ".join(valid_fields)}'
            )
            return

        new_value = None
        if field in ['name', 'anime']:
            new_value = raw_value.replace('-', ' ').title()
        elif field == 'rarity':
            rarity_data, error_msg = validate_rarity(raw_value)
            if error_msg:
                await update.message.reply_text(error_msg)
                return
            rarity_num, rarity_display = rarity_data
            new_value = rarity_display
        elif field == 'img_url':
            if not validate_image_url(raw_value):
                await update.message.reply_text(
                    '❌ Invalid Image URL.\n\n'
                    'Supported: Catbox, Telegraph, Imgur, etc.\n'
                    '❌ Telegram File IDs are not supported!'
                )
                return
            new_value = raw_value

        await collection.find_one_and_update(
            {'id': char_id},
            {'$set': {field: new_value}}
        )
        logger.info(f"Character {char_id} field '{field}' updated in database")

        updated_character = await collection.find_one({'id': char_id})
        caption = build_caption(
            updated_character['id'],
            updated_character['name'],
            updated_character['anime'],
            updated_character['rarity'],
            user_id,
            update.effective_user.first_name
        )

        channel_update_success = False
        if 'message_id' in character:
            if field == 'img_url':
                channel_update_success = await update_channel_media(
                    context, character['message_id'], new_value, caption
                )
            else:
                channel_update_success = await update_channel_caption(
                    context, character['message_id'], caption
                )
            
            if channel_update_success:
                logger.info(f"Character {char_id} updated in channel (message_id {character['message_id']})")
        else:
            logger.warning(f"Character {char_id} has no message_id, skipping channel update")

        if channel_update_success:
            await update.message.reply_text(
                'Updated in Database and Channel. '
                'Note: Caption edits may take a moment to appear.'
            )
        else:
            await update.message.reply_text(
                'Updated in Database, but Channel update failed or no message_id found. '
                'Database remains the source of truth.'
            )

    except Exception as e:
        logger.error(f"Update command error (user {user_id}, char_id {args[0] if args else 'unknown'}): {e}")
        await update.message.reply_text(
            'Update failed. Possible causes:\n'
            '- Bot not added to channel\n'
            '- Character has no channel message\n'
            '- Wrong character ID\n'
            f'Database update may have succeeded. Check logs.'
        )


UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)

DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)

UPDATE_HANDLER = CommandHandler('update', update_command, block=False)
application.add_handler(UPDATE_HANDLER)

shutdown_handler = ShutdownHandler(application)
shutdown_handler.setup()

logger.info("Bot handlers registered and shutdown handler configured")
