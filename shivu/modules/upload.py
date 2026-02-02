import asyncio
import aiohttp
import shlex
from typing import Optional, Dict, Any
from pymongo import ReturnDocument
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

RARITY_MAP: Dict[int, tuple] = {
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

WRONG_FORMAT_TEXT = """❌ Wrong format!

Usage: /upload "img_url" "character name" "anime" rarity_number

Example: /upload "https://example.com/image.jpg" "Muzan Kibutsuji" "Demon Slayer" 3

Rarity Numbers:
1-⚪ ᴄᴏᴍᴍᴏɴ | 2-🔵 ʀᴀʀᴇ | 3-🟡 ʟᴇɢᴇɴᴅᴀʀʏ | 4-💮 ꜱᴘᴇᴄɪᴀʟ
5-👹 ᴀɴᴄɪᴇɴᴛ | 6-🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ | 7-🔮 ᴇᴘɪᴄ | 8-🪐 ᴄᴏꜱᴍɪᴄ
9-⚰️ ɴɪɢʜᴛᴍᴀʀᴇ | 10-🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ | 11-💝 ᴠᴀʟᴇɴᴛɪɴᴇ | 12-🌸 ꜱᴘʀɪɴɢ
13-🏖️ ᴛʀᴏᴘɪᴄᴀʟ | 14-🍭 ᴋᴀᴡᴀɪɪ | 15-🧬 ʜʏʙʀɪᴅ"""

MAX_FILE_SIZE = 10 * 1024 * 1024


class AioHttpSessionManager:
    """Singleton manager for aiohttp session"""
    _session: Optional[aiohttp.ClientSession] = None
    
    @classmethod
    async def get_session(cls) -> aiohttp.ClientSession:
        if cls._session is None or cls._session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            cls._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=10),
                timeout=timeout
            )
        return cls._session
    
    @classmethod
    async def close_session(cls) -> None:
        if cls._session and not cls._session.closed:
            await cls._session.close()
            cls._session = None


async def get_next_sequence_number(sequence_name: str) -> int:
    """Get next sequential ID with auto-increment"""
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['sequence_value']


async def validate_and_check_url(url: str) -> tuple[bool, Optional[str]]:
    """Validate image URL and check accessibility"""
    if not url.startswith(('http://', 'https://')):
        return False, "Invalid URL format"

    try:
        session = await AioHttpSessionManager.get_session()
        
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                return False, f"URL not accessible (Status: {response.status})"
            
            content_type = response.headers.get('Content-Type', '').lower()
            valid_types = ['image/jpeg', 'image/jpg', 'image/png', 
                          'image/gif', 'image/webp', 'image/svg+xml']
            
            if not any(img_type in content_type for img_type in valid_types):
                return False, f"URL does not point to a valid image. Got: {content_type}"
            
            content = await response.read()
            size = len(content)
            
            if size > MAX_FILE_SIZE:
                return False, f"Image size ({size / (1024*1024):.2f} MB) exceeds 10 MB limit"
            
            if size < 1024:
                return False, "File too small to be a valid image"
            
            return True, None
            
    except asyncio.TimeoutError:
        return False, "URL request timed out - try a faster server"
    except aiohttp.ClientError as e:
        return False, f"Network error: {str(e)}"
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


def parse_arguments(text: str) -> list:
    """Parse command arguments using shlex for proper quoting"""
    try:
        return shlex.split(text)
    except ValueError as e:
        raise ValueError(f"Invalid argument format: {str(e)}")


async def upload(update: Update, context: CallbackContext) -> None:
    """Upload a new character to the database and channel"""
    user_id = str(update.effective_user.id)
    
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('❌ You need permission to use this command.')
        return

    try:
        # Parse arguments with shlex
        try:
            args = parse_arguments(update.message.text)[1:]  # Skip command
        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}\n\n{WRONG_FORMAT_TEXT}")
            return
        
        if len(args) != 4:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return
        
        img_url, character_name, anime, rarity_str = args
        
        # Validate rarity
        try:
            rarity_num = int(rarity_str)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(
                    f'❌ Invalid rarity number. Use numbers 1-15.\n\n{WRONG_FORMAT_TEXT}'
                )
                return
            rarity_value, rarity_name = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number!')
            return
        
        # Check for duplicate URL
        existing = await collection.find_one({'img_url': img_url})
        if existing:
            await update.message.reply_text(
                f'❌ This image URL already exists in database!\n'
                f'Character: {existing["name"]}\nID: {existing["id"]}'
            )
            return
        
        # Validate URL
        is_valid, error_msg = await validate_and_check_url(img_url)
        if not is_valid:
            await update.message.reply_text(f'❌ URL Validation Failed: {error_msg}')
            return
        
        # Generate ID with 6 digits
        next_id = await get_next_sequence_number('character_id')
        character_id = str(next_id).zfill(6)
        
        # Format caption
        rarity_emoji = rarity_name.split()[0]
        rarity_text = ' '.join(rarity_name.split()[1:])
        caption = (
            f"{character_id}: {character_name}\n"
            f"{anime}\n"
            f"{rarity_emoji}𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n\n"
            f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={update.effective_user.id}'>"
            f"{update.effective_user.first_name}</a>"
        )
        
        # Try to send to channel first
        try:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=caption,
                parse_mode='HTML'
            )
        except Exception as e:
            await update.message.reply_text(f'❌ Failed to upload to channel: {str(e)}')
            return
        
        # Only insert to database after successful Telegram message
        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity_name,
            'id': character_id,
            'message_id': message.message_id,
            'added_by': update.effective_user.id,
            'added_by_name': update.effective_user.first_name
        }
        
        await collection.insert_one(character)
        await update.message.reply_text(
            f'✅ CHARACTER ADDED SUCCESSFULLY!\n'
            f'ID: {character_id}\n'
            f'Name: {character_name}\n'
            f'Rarity: {rarity_text}'
        )
        
    except Exception as e:
        await update.message.reply_text(
            f'❌ Upload failed: {str(e)}\n\n'
            f'Report to: {SUPPORT_CHAT}'
        )


async def delete(update: Update, context: CallbackContext) -> None:
    """Delete a character from database and channel"""
    user_id = str(update.effective_user.id)
    
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('❌ You need permission to use this command.')
        return

    try:
        # Parse arguments
        try:
            args = parse_arguments(update.message.text)[1:]
        except ValueError:
            await update.message.reply_text('❌ Invalid argument format!')
            return
        
        if len(args) != 1:
            await update.message.reply_text('❌ Incorrect format!\n\nUsage: /delete <ID>')
            return
        
        character_id = args[0]
        
        # Find and delete character
        character = await collection.find_one_and_delete({'id': character_id})
        
        if not character:
            await update.message.reply_text('❌ Character not found!')
            return
        
        # Try to delete from channel
        try:
            await context.bot.delete_message(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id']
            )
            await update.message.reply_text('✅ Character deleted successfully!')
        except Exception as e:
            await update.message.reply_text(
                f'⚠️ Deleted from database, but failed to delete from channel: {str(e)}'
            )
            
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')


async def update_character(update: Update, context: CallbackContext) -> None:
    """Update character information"""
    user_id = str(update.effective_user.id)
    
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('❌ You need permission to use this command.')
        return

    try:
        # Parse arguments
        try:
            args = parse_arguments(update.message.text)[1:]
        except ValueError:
            await update.message.reply_text('❌ Invalid argument format!')
            return
        
        if len(args) != 3:
            await update.message.reply_text(
                '❌ Incorrect format!\n\n'
                'Usage: /update <id> <field> "<new_value>"\n'
                'Fields: img_url, name, anime, rarity'
            )
            return
        
        character_id, field, new_value = args
        
        # Validate field
        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if field not in valid_fields:
            await update.message.reply_text(
                f'❌ Invalid field! Use: {", ".join(valid_fields)}'
            )
            return
        
        # Find character
        character = await collection.find_one({'id': character_id})
        if not character:
            await update.message.reply_text('❌ Character not found!')
            return
        
        # Validate and process new value based on field
        if field in ['name', 'anime']:
            processed_value = new_value  # Keep original formatting
        elif field == 'rarity':
            try:
                rarity_num = int(new_value)
                if rarity_num not in RARITY_MAP:
                    await update.message.reply_text('❌ Invalid rarity! Use 1-15')
                    return
                _, processed_value = RARITY_MAP[rarity_num]
            except ValueError:
                await update.message.reply_text('❌ Rarity must be a number!')
                return
        else:  # img_url
            processed_value = new_value
            # Validate URL if it's a new one
            if processed_value != character.get('img_url'):
                is_valid, error_msg = await validate_and_check_url(processed_value)
                if not is_valid:
                    await update.message.reply_text(f'❌ URL Validation Failed: {error_msg}')
                    return
                
                # Check for duplicate URL
                existing = await collection.find_one({
                    'img_url': processed_value,
                    'id': {'$ne': character_id}
                })
                if existing:
                    await update.message.reply_text(
                        f'❌ This URL already exists!\n'
                        f'Character: {existing["name"]}\n'
                        f'ID: {existing["id"]}'
                    )
                    return
        
        # Update character data
        updated_character = character.copy()
        updated_character[field] = processed_value
        
        # Format new caption
        rarity_emoji = updated_character['rarity'].split()[0]
        rarity_text = ' '.join(updated_character['rarity'].split()[1:])
        caption = (
            f"{updated_character['id']}: {updated_character['name']}\n"
            f"{updated_character['anime']}\n"
            f"{rarity_emoji}𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n\n"
            f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={update.effective_user.id}'>"
            f"{update.effective_user.first_name}</a>"
        )
        
        success = False
        
        # Handle different update scenarios
        if field == 'img_url':
            # Use InputMediaPhoto for proper media editing
            try:
                media = InputMediaPhoto(
                    media=processed_value,
                    caption=caption,
                    parse_mode='HTML'
                )
                
                await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    media=media
                )
                success = True
            except Exception:
                # If editing fails, delete old and send new message
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=character['message_id']
                    )
                    
                    new_message = await context.bot.send_photo(
                        chat_id=CHARA_CHANNEL_ID,
                        photo=processed_value,
                        caption=caption,
                        parse_mode='HTML'
                    )
                    
                    # Update with new message ID
                    updated_character['message_id'] = new_message.message_id
                    success = True
                except Exception as e:
                    await update.message.reply_text(
                        f'❌ Failed to update message: {str(e)}'
                    )
                    return
        else:
            # Just update caption
            try:
                await context.bot.edit_message_caption(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    caption=caption,
                    parse_mode='HTML'
                )
                success = True
            except Exception as e:
                await update.message.reply_text(
                    f'❌ Failed to update caption: {str(e)}'
                )
                return
        
        # Only update database if Telegram update was successful
        if success:
            await collection.find_one_and_update(
                {'id': character_id},
                {'$set': {field: processed_value}}
            )
            await update.message.reply_text('✅ Character updated successfully!')
            
    except Exception as e:
        await update.message.reply_text(f'❌ Update failed: {str(e)}')


async def on_shutdown(application) -> None:
    """Close aiohttp session on bot shutdown"""
    await AioHttpSessionManager.close_session()


# Register handlers
UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
DELETE_HANDLER = CommandHandler('delete', delete, block=False)
UPDATE_HANDLER = CommandHandler('update', update_character, block=False)

application.add_handler(UPLOAD_HANDLER)
application.add_handler(DELETE_HANDLER)
application.add_handler(UPDATE_HANDLER)