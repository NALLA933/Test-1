import logging
import json
import os
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from bson import ObjectId

from shivu import (
    application,
    collection,
    user_totals_collection,
    user_collection,
    group_user_totals_collection,
    top_global_groups_collection,
    pm_users,
    user_balance_coll,
    shivuu
)

LOGGER = logging.getLogger(__name__)

# Backup settings
BACKUP_CHAT_ID = -1003702395415
AUTHORIZED_BACKUP_USER = 7818323042

# ---------------- BACKUP FUNCTIONS ---------------- #

async def create_database_backup():
    """
    Creates a complete backup of all database collections.
    Returns a dictionary with all data.
    """
    backup_data = {
        'timestamp': datetime.now().isoformat(),
        'collections': {}
    }
    
    collections_to_backup = {
        'anime_characters': collection,
        'user_totals': user_totals_collection,
        'user_collection': user_collection,
        'group_user_totals': group_user_totals_collection,
        'top_global_groups': top_global_groups_collection,
        'pm_users': pm_users,
        'user_balance': user_balance_coll
    }
    
    for coll_name, coll in collections_to_backup.items():
        try:
            data = await coll.find({}).to_list(length=None)
            # Convert ObjectId to string for JSON serialization
            for doc in data:
                if '_id' in doc:
                    doc['_id'] = str(doc['_id'])
            backup_data['collections'][coll_name] = data
            LOGGER.info(f"Backed up {len(data)} documents from {coll_name}")
        except Exception as e:
            LOGGER.error(f"Error backing up {coll_name}: {e}")
            backup_data['collections'][coll_name] = {'error': str(e)}
    
    return backup_data

async def restore_database_backup(backup_data):
    """
    Restores database from backup data.
    """
    collections_map = {
        'anime_characters': collection,
        'user_totals': user_totals_collection,
        'user_collection': user_collection,
        'group_user_totals': group_user_totals_collection,
        'top_global_groups': top_global_groups_collection,
        'pm_users': pm_users,
        'user_balance': user_balance_coll
    }
    
    restored_counts = {}
    
    for coll_name, data in backup_data['collections'].items():
        if coll_name in collections_map and isinstance(data, list):
            try:
                coll = collections_map[coll_name]
                
                # Clear existing data
                await coll.delete_many({})
                
                # Insert backup data
                if data:
                    # Convert string _id back to ObjectId if needed
                    for doc in data:
                        if '_id' in doc and isinstance(doc['_id'], str):
                            try:
                                doc['_id'] = ObjectId(doc['_id'])
                            except:
                                # If conversion fails, remove _id and let MongoDB create new one
                                del doc['_id']
                    
                    await coll.insert_many(data)
                    restored_counts[coll_name] = len(data)
                    LOGGER.info(f"Restored {len(data)} documents to {coll_name}")
                else:
                    restored_counts[coll_name] = 0
                    
            except Exception as e:
                LOGGER.error(f"Error restoring {coll_name}: {e}")
                restored_counts[coll_name] = f"Error: {str(e)}"
    
    return restored_counts

async def auto_backup_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Automatic backup job that runs periodically.
    Sends backup file to the specified chat.
    """
    try:
        LOGGER.info("Starting automatic database backup...")
        backup_data = await create_database_backup()
        
        # Create backup file
        filename = f"db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = f"/tmp/{filename}"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        # Send to backup chat
        total_docs = sum(len(v) if isinstance(v, list) else 0 
                        for v in backup_data['collections'].values())
        
        caption = (
            f"🔄 **Automatic Database Backup**\n\n"
            f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Total Documents: {total_docs}\n"
            f"💾 Collections: {len(backup_data['collections'])}\n\n"
            f"Use /restore to restore this backup."
        )
        
        await context.bot.send_document(
            chat_id=BACKUP_CHAT_ID,
            document=open(filepath, 'rb'),
            caption=caption,
            filename=filename
        )
        
        LOGGER.info(f"Backup sent successfully to {BACKUP_CHAT_ID}")
        
        # Clean up temp file
        os.remove(filepath)
        
    except Exception as e:
        LOGGER.error(f"Error in auto backup job: {e}")
        try:
            await context.bot.send_message(
                chat_id=BACKUP_CHAT_ID,
                text=f"❌ Backup failed: {str(e)}"
            )
        except:
            pass

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manual backup command - /backup
    Only authorized user can use this.
    """
    user_id = update.effective_user.id
    
    if user_id != AUTHORIZED_BACKUP_USER:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    try:
        status_msg = await update.message.reply_text("🔄 Creating database backup...")
        
        backup_data = await create_database_backup()
        
        # Create backup file
        filename = f"db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = f"/tmp/{filename}"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        # Calculate stats
        total_docs = sum(len(v) if isinstance(v, list) else 0 
                        for v in backup_data['collections'].values())
        file_size = os.path.getsize(filepath) / (1024 * 1024)  # MB
        
        caption = (
            f"✅ **Manual Database Backup**\n\n"
            f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Total Documents: {total_docs}\n"
            f"💾 Collections: {len(backup_data['collections'])}\n"
            f"📦 File Size: {file_size:.2f} MB\n\n"
            f"Use /restore to restore this backup."
        )
        
        await update.message.reply_document(
            document=open(filepath, 'rb'),
            caption=caption,
            filename=filename
        )
        
        await status_msg.delete()
        
        # Clean up temp file
        os.remove(filepath)
        
        LOGGER.info(f"Manual backup created by user {user_id}")
        
    except Exception as e:
        LOGGER.error(f"Error in backup command: {e}")
        await update.message.reply_text(f"❌ Backup failed: {str(e)}")

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Restore database from backup file - /restore
    Reply to a backup file with this command.
    Only authorized user can use this.
    """
    user_id = update.effective_user.id
    
    if user_id != AUTHORIZED_BACKUP_USER:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    # Check if replying to a document
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "❌ Please reply to a backup file with /restore command."
        )
        return
    
    try:
        status_msg = await update.message.reply_text("🔄 Downloading backup file...")
        
        # Download the file
        file = await update.message.reply_to_message.document.get_file()
        filepath = f"/tmp/restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        await file.download_to_drive(filepath)
        
        await status_msg.edit_text("📖 Reading backup data...")
        
        # Read backup data
        with open(filepath, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        # Verify backup structure
        if 'collections' not in backup_data:
            await status_msg.edit_text("❌ Invalid backup file format.")
            os.remove(filepath)
            return
        
        await status_msg.edit_text("⚠️ Restoring database... This may take a while.")
        
        # Restore database
        restored_counts = await restore_database_backup(backup_data)
        
        # Build response message
        total_restored = sum(v for v in restored_counts.values() if isinstance(v, int))
        
        response = f"✅ **Database Restored Successfully**\n\n"
        response += f"📅 Backup Date: {backup_data.get('timestamp', 'Unknown')}\n"
        response += f"📊 Total Documents Restored: {total_restored}\n\n"
        response += "**Collection Details:**\n"
        
        for coll_name, count in restored_counts.items():
            if isinstance(count, int):
                response += f"• {coll_name}: {count} documents\n"
            else:
                response += f"• {coll_name}: {count}\n"
        
        await status_msg.edit_text(response)
        
        # Clean up temp file
        os.remove(filepath)
        
        LOGGER.info(f"Database restored by user {user_id}")
        
    except Exception as e:
        LOGGER.error(f"Error in restore command: {e}")
        await update.message.reply_text(f"❌ Restore failed: {str(e)}")
        if os.path.exists(filepath):
            os.remove(filepath)

# ---------------- SETUP FUNCTION ---------------- #

def setup_backup_system():
    """
    Sets up the backup system with commands and scheduled jobs.
    Call this from your main bot file.
    """
    # Add command handlers
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    
    # Schedule auto backup job (runs every 1 hour)
    job_queue = application.job_queue
    job_queue.run_repeating(
        auto_backup_job,
        interval=3600,  # 1 hour in seconds
        first=10  # First run after 10 seconds of bot start
    )
    
    LOGGER.info("Backup system initialized with auto-backup every 1 hour")

# Auto-initialize when imported
setup_backup_system()
