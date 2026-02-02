import urllib.request
import logging
import re
from pymongo import ReturnDocument
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext
from telegram.error import TelegramError

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

def is_sudo(user_id: int) -> bool:
    # Use Config values (integers) directly
    try:
        return user_id == Config.OWNER_ID or user_id in Config.SUDO_USERS
    except Exception:
        # Fallback to deny if config is not present or malformed
        return False

def validate_rarity(rarity_input: str) -> tuple:
    try:
        rarity_num = int(rarity_input)
        if rarity_num not in RARITY_MAP:
            error_msg = "Invalid rarity! Use numbers 1-15.\n\n" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])
            return None, error_msg
        return RARITY_MAP[rarity_num], None
    except ValueError:
        error_msg = "Rarity must be a number! Use 1-15.\n\n" + "\n".join([f"{k}: {v[1]}" for k, v in RARITY_MAP.items()])
        return None, error_msg

def validate_image_url(url: str) -> bool:
    """
    Validates image URLs from various sources (Catbox, Telegraph, Imgur, etc.)
    Blocks Telegram file IDs
    """
    if not url:
        return False
    
    # Block Telegram file IDs (they don't start with http/https)
    if not url.startswith(('http://', 'https://')):
        return False
    
    # Supported image hosting services
    supported_domains = [
        'catbox.moe',
        'files.catbox.moe',
        'telegra.ph',
        'telegraph',
        'imgur.com',
        'i.imgur.com',
        'postimg.cc',
        'i.postimg.cc',
        'ibb.co',
        'i.ibb.co',
        'imgbb.com',
        'i.imgbb.com',
        'imageban.ru',
        'picsum.photos',
        'unsplash.com',
        'images.unsplash.com',
    ]
    
    # Check if URL contains any supported domain
    url_lower = url.lower()
    is_supported = any(domain in url_lower for domain in supported_domains)
    
    # If it's a supported domain, do a quick format check
    if is_supported:
        # Check if it has a valid image extension or is from a known image host
        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
        has_valid_ext = any(url_lower.endswith(ext) for ext in valid_extensions)
        
        # Telegraph URLs don't always have extensions, so we allow them
        is_telegraph = 'telegra.ph' in url_lower
        
        if has_valid_ext or is_telegraph:
            return True
    
    # For other URLs, try to open and verify
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            content_type = response.headers.get('Content-Type', '').lower()
            # Check if it's an image content type
            return content_type.startswith('image/')
    except Exception as e:
        logger.warning(f"URL validation failed for {url}: {e}")
        return False

def build_caption(char_id: str, char_name: str, anime: str, rarity_display: str, uploader_id: int, uploader_name: str) -> str:
    """
    Builds caption in the format:
    86: Roronoa Zoro
    One Piece
    ⚰️ 𝙍𝘼𝙍𝙄𝙏𝙔: ɴɪɢʜᴛᴍᴀʀᴇ
    Type: 🖼 Image
    𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ 𝘼 𝙇 𝙀 𝙓
    """
    emoji = rarity_display.split()[0]
    rarity_text = rarity_display.split()[1]
    
    return (
        f"{char_id}: {char_name}\n"
        f"{anime}\n"
        f"{emoji} 𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n"
        f"Type: 🖼 Image\n"
        f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ 𝘼 𝙇 𝙀 𝙓"
    )

async def get_next_sequence_number(sequence_name):
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        return_document=ReturnDocument.AFTER
    )
    if not sequence_document:
        await sequence_collection.insert_one({'_id': sequence_name, 'sequence_value': 0})
        return 0
    return sequence_document['sequence_value']

async def upload(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
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
            await update.message.reply_text('❌ Invalid Image URL.\n\nSupported: Catbox, Telegraph, Imgur, etc.\n❌ Telegram File IDs are not supported!')
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
            char_id,
            character_name,
            anime,
            rarity_display,
            update.effective_user.id,
            update.effective_user.first_name
        )

        try:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=caption,
                parse_mode='HTML'
            )
            character['message_id'] = message.message_id
            await collection.insert_one(character)
            await update.message.reply_text('CHARACTER ADDED....')
        except Exception as channel_error:
            logger.error(f"Channel upload error: {channel_error}")
            await collection.insert_one(character)
            await update.message.reply_text("Character Added but no Database Channel Found, Consider adding one.")

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.reply_text(f'Character Upload Unsuccessful. Error: {str(e)}\nIf you think this is a source error, forward to: {SUPPORT_CHAT}')

async def delete(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format... Please use: /delete ID')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if not character:
            await update.message.reply_text('Character not found.')
            return

        try:
            if 'message_id' in character:
                await context.bot.delete_message(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id']
                )
            await update.message.reply_text('DONE')
        except TelegramError as e:
            logger.warning(f"Channel message deletion failed: {e}")
            await update.message.reply_text('Deleted Successfully from db, but character not found In Channel')

    except Exception as e:
        logger.error(f"Delete error: {e}")
        await update.message.reply_text(f'{str(e)}')

async def update(update: Update, context: CallbackContext) -> None:
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('Incorrect format. Please use: /update id field new_value')
            return

        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text('Character not found.')
            return

        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'Invalid field. Please use one of the following: {", ".join(valid_fields)}')
            return

        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()
        elif args[1] == 'rarity':
            rarity_data, error_msg = validate_rarity(args[2])
            if error_msg:
                await update.message.reply_text(error_msg)
                return
            rarity_num, rarity_display = rarity_data
            new_value = rarity_display
        elif args[1] == 'img_url':
            if not validate_image_url(args[2]):
                await update.message.reply_text('❌ Invalid Image URL.\n\nSupported: Catbox, Telegraph, Imgur, etc.\n❌ Telegram File IDs are not supported!')
                return
            new_value = args[2]
        else:
            new_value = args[2]

        await collection.find_one_and_update({'id': args[0]}, {'$set': {args[1]: new_value}})

        updated_character = await collection.find_one({'id': args[0]})

        caption = build_caption(
            updated_character['id'],
            updated_character['name'],
            updated_character['anime'],
            updated_character['rarity'],
            update.effective_user.id,
            update.effective_user.first_name
        )

        if args[1] == 'img_url':
            try:
                if 'message_id' in character:
                    await context.bot.edit_message_media(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id'],
                        media=InputMediaPhoto(media=new_value, caption=caption, parse_mode='HTML')
                    )
                else:
                    message = await context.bot.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=new_value,
                        caption=caption,
                        parse_mode='HTML'
                    )
                    await collection.find_one_and_update({'id': args[0]}, {'$set': {'message_id': message.message_id}})
            except Exception as media_error:
                logger.error(f"Media update error: {media_error}")
        else:
            try:
                if 'message_id' in character:
                    await context.bot.edit_message_caption(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id'],
                        caption=caption,
                        parse_mode='HTML'
                    )
            except Exception as caption_error:
                logger.error(f"Caption update error: {caption_error}")

        await update.message.reply_text('Updated Done in Database.... But sometimes it Takes Time to edit Caption in Your Channel..So wait..')

    except Exception as e:
        logger.error(f"Update error: {e}")
        await update.message.reply_text(f'I guess did not added bot in channel.. or character uploaded Long time ago.. Or character not exits.. orr Wrong id')

UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)
DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)
UPDATE_HANDLER = CommandHandler('update', update, block=False)
application.add_handler(UPDATE_HANDLER)
