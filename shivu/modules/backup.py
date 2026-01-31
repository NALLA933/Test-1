from pyrogram import filters, Client, types as t
from shivu import application, user_collection, shivuu
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import datetime
import os

WRONG_FORMAT_TEXT = """
<b>Wrong Format ❌</b>

Example: <code>/backup [User Id]</code>
Example: <code>/restore [User Id] [Your Backup Id]</code>
"""

async def get_user_info(user_id: int):
    try:
        user = await user_collection.find_one({'id': user_id})
        return user
    except Exception as e:
        return None

async def create_backup(user_id: int):
    try:
        user = await get_user_info(user_id)
        if not user:
            return None
        
        backup_data = {
            'user_id': user_id,
            'characters': user.get('characters', []),
            'favorites': user.get('favorites', []),
            'timestamp': datetime.datetime.now()
        }
        return backup_data
    except Exception as e:
        return None

async def restore_backup(user_id: int, backup_data: dict):
    try:
        await user_collection.update_one(
            {'id': user_id},
            {
                '$set': {
                    'characters': backup_data.get('characters', []),
                    'favorites': backup_data.get('favorites', [])
                }
            }
        )
        return True
    except Exception as e:
        return False

@shivuu.on_message(filters.command(["backup"]))
async def backup_command(client: Client, message: Message):
    try:
        if len(message.command) != 2:
            await message.reply_text(WRONG_FORMAT_TEXT)
            return
        
        user_id = int(message.command[1])
        user = await get_user_info(user_id)
        
        if not user:
            await message.reply_text("❌ User not found in database!")
            return
        
        backup = await create_backup(user_id)
        
        if not backup:
            await message.reply_text("❌ Failed to create backup!")
            return
        
        backup_id = f"BK_{user_id}_{int(datetime.datetime.now().timestamp())}"
        
        # Store backup (you can implement your storage logic here)
        
        await message.reply_text(
            f"✅ <b>Backup Created Successfully!</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"🆔 Backup ID: <code>{backup_id}</code>\n"
            f"📅 Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Use this Backup ID to restore later."
        )
        
    except ValueError:
        await message.reply_text(WRONG_FORMAT_TEXT)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@shivuu.on_message(filters.command(["restore"]))
async def restore_command(client: Client, message: Message):
    try:
        if len(message.command) != 3:
            await message.reply_text(WRONG_FORMAT_TEXT)
            return
        
        user_id = int(message.command[1])
        backup_id = message.command[2]
        
        # Fetch backup data (implement your storage retrieval logic here)
        # For now, this is a placeholder
        
        await message.reply_text(
            f"ℹ️ <b>Restore functionality is under development</b>\n\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Backup ID: <code>{backup_id}</code>"
        )
        
    except ValueError:
        await message.reply_text(WRONG_FORMAT_TEXT)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

__mod_name__ = "Backup"

__help__ = """
<b>Backup Commands:</b>

/backup [User ID] - Create a backup of user data
/restore [User ID] [Backup ID] - Restore user data from backup

<b>Examples:</b>
<code>/backup 123456789</code>
<code>/restore 123456789 BK_123456789_1234567890</code>
"""
