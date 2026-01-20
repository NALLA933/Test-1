import asyncio
from typing import Dict, Any, Optional

import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from telegram.error import BadRequest

from shivu import application, sudo_users, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT

# Constants
WRONG_FORMAT_TEXT = """Wrong ❌️ format...  eg. /upload Img_url muzan-kibutsuji Demon-slayer 3

img_url character-name anime-name rarity-number

use rarity number accordingly rarity Map

rarity_map = 1 (⚪️ Common), 2 (🟣 Rare), 3 (🟡 Legendary), 4 (🟢 Medium), 5 (💮 Special Edition)"""

RARITY_MAP = {
    1: "⚪ Common",
    2: "🟣 Rare", 
    3: "🟡 Legendary",
    4: "🟢 Medium",
    5: "💮 Special Edition"
}

async def validate_image_url(url: str) -> bool:
    """Validate if URL is accessible and points to an image."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.head(url) as response:
                if response.status != 200:
                    return False
                
                # Check if content type is image
                content_type = response.headers.get('Content-Type', '').lower()
                return content_type.startswith('image/')
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False

async def get_next_sequence_number(sequence_name: str) -> int:
    """Get next sequence number for character IDs."""
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name},
        {'$inc': {'sequence_value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return sequence_document['sequence_value']

async def send_channel_message(
    context: CallbackContext, 
    character: Dict[str, Any], 
    user_id: int, 
    user_name: str,
    action: str = "Added"
) -> Optional[int]:
    """Send or edit character message in channel."""
    try:
        caption = (
            f"<b>Character Name:</b> {character['name']}\n"
            f"<b>Anime Name:</b> {character['anime']}\n"
            f"<b>Rarity:</b> {character['rarity']}\n"
            f"<b>ID:</b> {character['id']}\n"
            f"{action} by <a href='tg://user?id={user_id}'>{user_name}</a>"
        )
        
        if action == "Added" or 'message_id' not in character:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
            return message.message_id
        else:
            await context.bot.edit_message_caption(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id'],
                caption=caption,
                parse_mode='HTML'
            )
            return character['message_id']
    except BadRequest as e:
        if "not found" in str(e).lower() or "message to edit not found" in str(e).lower():
            # Message was deleted from channel, send new one
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
            return message.message_id
        raise

async def upload(update: Update, context: CallbackContext) -> None:
    """Handle character upload command."""
    if update.effective_user.id not in sudo_users:
        await update.message.reply_text('Ask My Owner...')
        return

    # Validate arguments
    if not context.args or len(context.args) != 4:
        await update.message.reply_text(WRONG_FORMAT_TEXT)
        return

    img_url, char_raw, anime_raw, rarity_raw = context.args

    # Validate image URL
    if not await validate_image_url(img_url):
        await update.message.reply_text('Invalid or inaccessible image URL.')
        return

    # Parse rarity
    try:
        rarity_num = int(rarity_raw)
        if rarity_num not in RARITY_MAP:
            await update.message.reply_text('Invalid rarity. Please use 1, 2, 3, 4, or 5.')
            return
        rarity = RARITY_MAP[rarity_num]
    except ValueError:
        await update.message.reply_text('Rarity must be a number (1-5).')
        return

    # Generate character data
    character = {
        'img_url': img_url,
        'name': char_raw.replace('-', ' ').title(),
        'anime': anime_raw.replace('-', ' ').title(),
        'rarity': rarity,
        'id': str(await get_next_sequence_number('character_id')).zfill(6)
    }

    try:
        # Send to channel and get message ID
        message_id = await send_channel_message(
            context, character, 
            update.effective_user.id, 
            update.effective_user.first_name,
            "Added"
        )
        character['message_id'] = message_id
        
        # Insert into database
        await collection.insert_one(character)
        await update.message.reply_text('✅ CHARACTER ADDED SUCCESSFULLY!')
        
    except Exception as e:
        # Try to insert without channel message
        try:
            await collection.insert_one(character)
            await update.message.reply_text(
                "⚠️ Character added to database but failed to send to channel. "
                "Bot might not have permission to post in the channel."
            )
        except Exception as db_error:
            await update.message.reply_text(
                f'❌ Character upload failed completely.\n'
                f'Error: {str(db_error)}\n'
                f'If you think this is a source error, forward to: {SUPPORT_CHAT}'
            )

async def delete(update: Update, context: CallbackContext) -> None:
    """Handle character deletion command."""
    if update.effective_user.id not in sudo_users:
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text('Incorrect format... Please use: /delete ID')
        return

    character_id = context.args[0]
    
    # Find and delete character
    character = await collection.find_one_and_delete({'id': character_id})
    
    if not character:
        await update.message.reply_text('❌ Character not found in database.')
        return

    # Try to delete from channel
    try:
        if 'message_id' in character:
            await context.bot.delete_message(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id']
            )
            await update.message.reply_text('✅ Character deleted from database and channel.')
        else:
            await update.message.reply_text('✅ Character deleted from database (no channel message found).')
    except BadRequest as e:
        if "message to delete not found" in str(e).lower():
            await update.message.reply_text('✅ Character deleted from database (channel message was already gone).')
        else:
            await update.message.reply_text(
                f'✅ Character deleted from database.\n'
                f'⚠️ Could not delete from channel: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ Character deleted from database.\n'
            f'⚠️ Channel deletion error: {str(e)}'
        )

async def update(update: Update, context: CallbackContext) -> None:
    """Handle character update command."""
    if update.effective_user.id not in sudo_users:
        await update.message.reply_text('You do not have permission to use this command.')
        return

    if not context.args or len(context.args) != 3:
        await update.message.reply_text(
            'Incorrect format. Please use: /update id field new_value\n'
            'Valid fields: img_url, name, anime, rarity'
        )
        return

    char_id, field, new_value = context.args

    # Validate field
    valid_fields = ['img_url', 'name', 'anime', 'rarity']
    if field not in valid_fields:
        await update.message.reply_text(
            f'Invalid field. Valid fields: {", ".join(valid_fields)}'
        )
        return

    # Get existing character
    character = await collection.find_one({'id': char_id})
    if not character:
        await update.message.reply_text('❌ Character not found.')
        return

    # Process new value based on field type
    update_data = {}
    if field in ['name', 'anime']:
        update_data[field] = new_value.replace('-', ' ').title()
    elif field == 'rarity':
        try:
            rarity_num = int(new_value)
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text('Invalid rarity. Please use 1, 2, 3, 4, or 5.')
                return
            update_data[field] = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('Rarity must be a number (1-5).')
            return
    else:  # img_url
        if not await validate_image_url(new_value):
            await update.message.reply_text('Invalid or inaccessible image URL.')
            return
        update_data[field] = new_value

    # Update database
    updated_character = await collection.find_one_and_update(
        {'id': char_id},
        {'$set': update_data},
        return_document=ReturnDocument.AFTER
    )

    if not updated_character:
        await update.message.reply_text('❌ Failed to update character in database.')
        return

    # Update channel message
    try:
        if field == 'img_url':
            # For image URL changes, we need to send a new message
            if 'message_id' in updated_character:
                try:
                    await context.bot.delete_message(
                        chat_id=CHARA_CHANNEL_ID,
                        message_id=updated_character['message_id']
                    )
                except BadRequest:
                    pass  # Message might already be deleted
            
            new_message_id = await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )
            
            # Update message_id in database
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': new_message_id}}
            )
            
        elif 'message_id' in updated_character:
            # For other updates, edit existing message
            await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )
        
        await update.message.reply_text('✅ Character updated successfully!')
        
    except BadRequest as e:
        if "not found" in str(e).lower() or "message to edit not found" in str(e).lower():
            # Channel message was deleted, send new one
            new_message_id = await send_channel_message(
                context, updated_character,
                update.effective_user.id,
                update.effective_user.first_name,
                "Updated"
            )
            await collection.update_one(
                {'id': char_id},
                {'$set': {'message_id': new_message_id}}
            )
            await update.message.reply_text('✅ Character updated! (Recreated channel message)')
        else:
            await update.message.reply_text(
                f'✅ Database updated but channel update failed: {str(e)}'
            )
    except Exception as e:
        await update.message.reply_text(
            f'✅ Database updated but channel update failed: {str(e)}'
        )

# Register handlers
application.add_handler(CommandHandler("upload", upload))
application.add_handler(CommandHandler("delete", delete))
application.add_handler(CommandHandler("update", update))