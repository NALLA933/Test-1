"""
Backup Module for Shivu Bot
Location: shivu/modules/backup.py

Automatic database backup system with hourly backups sent to Telegram
"""

import os
import json
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId

LOGGER = logging.getLogger(__name__)

# Configuration
BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

# Aapki Telegram ID - Yaha backups send honge
BACKUP_RECEIVER_ID = 8453236527
OWNER_ID = 8453236527

scheduler = None

def convert_objectid(obj):
    """Convert MongoDB ObjectId to string for JSON serialization"""
    if isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, dict):
        return {key: convert_objectid(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_objectid(item) for item in obj]
    return obj

async def create_backup():
    """Create a complete database backup"""
    try:
        # Import database from shivu module
        from shivu import (
            collection,
            user_totals_collection,
            user_collection,
            group_user_totals_collection,
            top_global_groups_collection,
            pm_users,
            user_balance_coll
        )
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_data = {}

        LOGGER.info(f"Starting backup at {datetime.now()}")
        
        # Database collections with their references
        collections_map = {
            'anime_characters_lol': collection,
            'user_totals_lmaoooo': user_totals_collection,
            'user_collection_lmaoooo': user_collection,
            'group_user_totalsssssss': group_user_totals_collection,
            'top_global_groups': top_global_groups_collection,
            'total_pm_users': pm_users,
            'user_balance': user_balance_coll
        }

        total_documents = 0
        for col_name, col_ref in collections_map.items():
            try:
                documents = await col_ref.find({}).to_list(length=None)
                backup_data[col_name] = [convert_objectid(doc) for doc in documents]
                total_documents += len(documents)
                LOGGER.info(f"✅ Backed up {col_name}: {len(documents)} documents")
            except Exception as e:
                LOGGER.error(f"❌ Error backing up {col_name}: {e}")
                backup_data[col_name] = []

        # Backup file create karna
        backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.json")
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False, default=str)

        file_size = os.path.getsize(backup_file) / (1024 * 1024)  # MB mein
        
        # Purane backups cleanup (last 24 backups rakhenge)
        cleanup_old_backups(24)

        LOGGER.info(f"✅ Backup completed: {backup_file} ({file_size:.2f} MB, {total_documents} documents)")
        return backup_file, file_size, total_documents
    
    except Exception as e:
        LOGGER.error(f"❌ Backup creation failed: {e}", exc_info=True)
        return None, 0, 0

def cleanup_old_backups(keep=24):
    """Purane backups delete karna (sirf specified number hi rakhna)"""
    try:
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')])
        if len(backups) > keep:
            for old_backup in backups[:-keep]:
                old_file = os.path.join(BACKUP_DIR, old_backup)
                os.remove(old_file)
                LOGGER.info(f"🗑️ Deleted old backup: {old_backup}")
    except Exception as e:
        LOGGER.error(f"❌ Cleanup error: {e}")

async def restore_backup(backup_file):
    """Backup se database restore karna"""
    try:
        # Import database from shivu module
        from shivu import db
        
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)

        restored_collections = []
        total_restored = 0

        for collection_name, documents in backup_data.items():
            try:
                collection = db[collection_name]
                if documents:
                    # _id field remove karna taaki MongoDB naya ID assign kare
                    for doc in documents:
                        if '_id' in doc:
                            del doc['_id']
                    
                    # Documents insert karna
                    await collection.insert_many(documents)
                    restored_collections.append(f"{collection_name} ({len(documents)} docs)")
                    total_restored += len(documents)
                    LOGGER.info(f"✅ Restored {collection_name}: {len(documents)} documents")
            except Exception as e:
                LOGGER.error(f"❌ Error restoring {collection_name}: {e}")

        return True, restored_collections, total_restored
    except Exception as e:
        LOGGER.error(f"❌ Restore failed: {e}", exc_info=True)
        return False, [], 0

# ==================== Telegram Commands ====================

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual backup create karne ka command"""
    from shivu import sudo_users
    
    user_id = update.effective_user.id
    
    # Sirf owner aur sudo users hi manually backup create kar sakte hain
    if user_id not in sudo_users and user_id != OWNER_ID:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return

    msg = await update.message.reply_text("📦 Creating backup...")
    backup_file, file_size, total_docs = await create_backup()

    if backup_file:
        await msg.edit_text(
            f"✅ <b>Backup Created Successfully</b>\n\n"
            f"📁 File: <code>{os.path.basename(backup_file)}</code>\n"
            f"💾 Size: <b>{file_size:.2f} MB</b>\n"
            f"📊 Documents: <b>{total_docs}</b>\n"
            f"🕐 Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
            parse_mode='HTML'
        )
        try:
            with open(backup_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=os.path.basename(backup_file),
                    caption=f"📦 Manual Backup\n💾 {file_size:.2f} MB | 📊 {total_docs} docs"
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Backup created but couldn't send file: {e}")
    else:
        await msg.edit_text("❌ Backup failed. Check logs for details.")

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backup se restore karne ka command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only owner can restore database.")
        return

    # Check if replying to a backup file
    if update.message.reply_to_message and update.message.reply_to_message.document:
        msg = await update.message.reply_text("⬇️ Downloading backup file...")
        try:
            file = await update.message.reply_to_message.document.get_file()
            backup_file = os.path.join(BACKUP_DIR, update.message.reply_to_message.document.file_name)
            await file.download_to_drive(backup_file)

            await msg.edit_text("🔄 Restoring database... This may take a while.")
            success, restored, total_docs = await restore_backup(backup_file)

            if success:
                collections_text = "\n".join([f"• {col}" for col in restored])
                await msg.edit_text(
                    f"✅ <b>Restore Completed</b>\n\n"
                    f"📊 Total Documents: <b>{total_docs}</b>\n\n"
                    f"<b>Restored Collections:</b>\n{collections_text}",
                    parse_mode='HTML'
                )
            else:
                await msg.edit_text("❌ Restore failed. Check logs for details.")
        except Exception as e:
            await update.message.reply_text(f"❌ Restore error: {e}")
    else:
        # List available backups
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')], reverse=True)
        if backups:
            backup_list = "\n".join([f"• {b}" for b in backups[:10]])
            await update.message.reply_text(
                f"📋 <b>Available Backups:</b>\n\n{backup_list}\n\n"
                f"<i>Reply to a backup file with /restore to restore it</i>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("❌ No backups found.")

async def list_backups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Available backups ki list"""
    from shivu import sudo_users
    
    user_id = update.effective_user.id
    if user_id not in sudo_users and user_id != OWNER_ID:
        await update.message.reply_text("❌ You don't have permission.")
        return

    backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')], reverse=True)
    
    if backups:
        backup_info = []
        total_size = 0
        
        for backup in backups[:20]:  # Last 20 backups
            size = os.path.getsize(os.path.join(BACKUP_DIR, backup)) / (1024 * 1024)
            total_size += size
            backup_info.append(f"• {backup} ({size:.2f} MB)")

        await update.message.reply_text(
            f"📋 <b>Backup List</b>\n\n"
            f"📦 Total Backups: <b>{len(backups)}</b>\n"
            f"💾 Total Size: <b>{total_size:.2f} MB</b>\n\n"
            f"<b>Recent Backups:</b>\n" + "\n".join(backup_info),
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text("❌ No backups found.")

async def test_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backup system test karne ka command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only command.")
        return

    await update.message.reply_text("🧪 Testing backup system...")
    await hourly_backup_job(context.application)
    await update.message.reply_text("✅ Test completed. Check if backup was sent.")

async def backup_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backup system ka status check karna"""
    from shivu import sudo_users
    
    user_id = update.effective_user.id
    if user_id not in sudo_users and user_id != OWNER_ID:
        await update.message.reply_text("❌ You don't have permission.")
        return

    try:
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_')])
        if backups:
            latest_backup = backups[-1]
            latest_size = os.path.getsize(os.path.join(BACKUP_DIR, latest_backup)) / (1024 * 1024)
            
            # Next backup time calculate karna (assuming hourly backups)
            from datetime import timedelta
            next_backup = datetime.now() + timedelta(hours=1)
            next_backup = next_backup.replace(minute=0, second=0, microsecond=0)
            
            await update.message.reply_text(
                f"📊 <b>Backup System Status</b>\n\n"
                f"✅ Status: <b>Active</b>\n"
                f"📦 Total Backups: <b>{len(backups)}</b>\n"
                f"📁 Latest: <code>{latest_backup}</code>\n"
                f"💾 Size: <b>{latest_size:.2f} MB</b>\n"
                f"🔄 Frequency: <b>Every Hour</b>\n"
                f"⏰ Next Backup: <code>{next_backup.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                f"📬 Backup Receiver: <code>{BACKUP_RECEIVER_ID}</code>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"📊 <b>Backup System Status</b>\n\n"
                f"✅ Status: <b>Active</b>\n"
                f"❌ No backups created yet\n"
                f"🔄 Frequency: <b>Every Hour</b>\n"
                f"📬 Backup Receiver: <code>{BACKUP_RECEIVER_ID}</code>",
                parse_mode='HTML'
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Error getting status: {e}")

# ==================== Scheduled Backup Job ====================

async def hourly_backup_job(application):
    """Automatic hourly backup job jo specified ID par backup send karega"""
    try:
        LOGGER.info("🔄 Starting scheduled backup...")
        backup_file, file_size, total_docs = await create_backup()

        if backup_file:
            try:
                # Backup file send karna aapki ID par
                with open(backup_file, 'rb') as f:
                    await application.bot.send_document(
                        chat_id=BACKUP_RECEIVER_ID,
                        document=f,
                        filename=os.path.basename(backup_file),
                        caption=(
                            f"🤖 <b>Automated Hourly Backup</b>\n\n"
                            f"💾 Size: <b>{file_size:.2f} MB</b>\n"
                            f"📊 Documents: <b>{total_docs}</b>\n"
                            f"🕐 Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
                            f"✅ Backup completed successfully!"
                        ),
                        parse_mode='HTML'
                    )
                LOGGER.info(f"✅ Backup sent successfully to {BACKUP_RECEIVER_ID}")
            except Exception as e:
                LOGGER.error(f"❌ Failed to send backup to Telegram: {e}")
                # Agar send fail ho jaye toh error message bhejna
                await application.bot.send_message(
                    chat_id=BACKUP_RECEIVER_ID,
                    text=(
                        f"⚠️ <b>Backup Send Failed</b>\n\n"
                        f"Backup was created but couldn't be sent.\n"
                        f"Error: <code>{str(e)}</code>\n\n"
                        f"File: {os.path.basename(backup_file)}"
                    ),
                    parse_mode='HTML'
                )
        else:
            # Agar backup creation fail ho jaye
            await application.bot.send_message(
                chat_id=BACKUP_RECEIVER_ID,
                text=(
                    f"❌ <b>Backup Creation Failed</b>\n\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"Check server logs for details."
                ),
                parse_mode='HTML'
            )
    except Exception as e:
        LOGGER.error(f"❌ Backup job error: {e}", exc_info=True)
        try:
            await application.bot.send_message(
                chat_id=BACKUP_RECEIVER_ID,
                text=f"❌ Critical backup error: {str(e)}"
            )
        except:
            pass

# ==================== Module Handlers ====================

__mod_name__ = "Backup"

__help__ = """
🔧 *Backup System Commands:*

*Owner/Sudo Only:*
• `/backup` - Create manual database backup
• `/listbackups` - List all available backups
• `/backupstatus` - Check backup system status
• `/testbackup` - Test backup system

*Owner Only:*
• `/restore` - Restore from backup (reply to backup file)

*Features:*
✅ Automatic hourly backups
✅ Backups sent to configured Telegram ID
✅ Last 24 backups auto-saved
✅ Easy restore functionality
✅ Status monitoring

Note: Automatic backups run every hour and are sent to the configured admin.
"""

backup_handler = CommandHandler("backup", backup_command, block=False)
restore_handler = CommandHandler("restore", restore_command, block=False)
listbackups_handler = CommandHandler("listbackups", list_backups_command, block=False)
testbackup_handler = CommandHandler("testbackup", test_backup_command, block=False)
backupstatus_handler = CommandHandler("backupstatus", backup_status_command, block=False)

# ==================== Initialization Function ====================

def init_scheduler(application):
    """Initialize the backup scheduler"""
    global scheduler
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        hourly_backup_job,
        'interval',
        hours=1,  # Har ghante backup
        args=[application],
        id='hourly_backup',
        replace_existing=True
    )
    scheduler.start()
    
    LOGGER.info(f"✅ Backup scheduler initialized - Hourly backups will be sent to {BACKUP_RECEIVER_ID}")
    print(f"✅ Backup system ready - Backups → {BACKUP_RECEIVER_ID}")
