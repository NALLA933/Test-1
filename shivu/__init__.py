import logging
import asyncio
import json
from datetime import datetime
from pathlib import Path
from pyrogram import Client
from telegram.ext import Application
from motor.motor_asyncio import AsyncIOMotorClient

# ---------------- LOGGING ---------------- #

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.FileHandler("log.txt"), logging.StreamHandler()],
    level=logging.INFO,
)

logging.getLogger("apscheduler").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pyrate_limiter").setLevel(logging.ERROR)

LOGGER = logging.getLogger(__name__)

# ---------------- CONFIG ---------------- #

from shivu.config import Development as Config

API_ID = Config.API_ID
API_HASH = Config.API_HASH
TOKEN = Config.TOKEN
MONGO_URL = Config.MONGO_URL

OWNER_ID = Config.OWNER_ID
SUDO_USERS = Config.SUDO_USERS
GROUP_ID = Config.GROUP_ID
CHARA_CHANNEL_ID = Config.CHARA_CHANNEL_ID
VIDEO_URL = Config.VIDEO_URL
SUPPORT_CHAT = Config.SUPPORT_CHAT
UPDATE_CHAT = Config.UPDATE_CHAT
BOT_USERNAME = Config.BOT_USERNAME

# ---------------- TELEGRAM APP ---------------- #

application = Application.builder().token(TOKEN).build()

# ---------------- PYROGRAM ---------------- #

shivuu = Client(
    "Shivu",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=TOKEN,
)

# ---------------- DATABASE ---------------- #

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["Character_catcher"]

collection = db["anime_characters_lol"]
user_totals_collection = db["user_totals_lmaoooo"]
user_collection = db["user_collection_lmaoooo"]
group_user_totals_collection = db["group_user_totalsssssss"]
top_global_groups_collection = db["top_global_groups"]
pm_users = db["total_pm_users"]
user_balance_coll = db['user_balance']

# ---------------- DATABASE FUNCTIONS ---------------- #

async def change_balance(user_id: int, amount: int):
    """
    Update user's balance by a specified amount (can be positive or negative).
    Creates the user document if it doesn't exist.
    """
    await user_balance_coll.update_one(
        {'user_id': user_id},
        {'$inc': {'balance': amount}},
        upsert=True
    )

# ---------------- DATABASE BACKUP SYSTEM ---------------- #

class DatabaseBackup:
    """Auto backup system for database keys"""
    
    def __init__(self, backup_dir: str = "backups", backup_interval: int = 3600):
        """
        Initialize backup system
        
        Args:
            backup_dir: Directory to store backups
            backup_interval: Backup interval in seconds (default: 1 hour)
        """
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(exist_ok=True)
        self.backup_interval = backup_interval
        self.is_running = False
        
    async def backup_collection(self, coll, collection_name: str) -> dict:
        """Backup a single collection"""
        try:
            documents = []
            async for doc in coll.find():
                # Convert ObjectId to string for JSON serialization
                if '_id' in doc:
                    doc['_id'] = str(doc['_id'])
                documents.append(doc)
            
            LOGGER.info(f"✅ Backed up {len(documents)} documents from {collection_name}")
            return {
                "collection": collection_name,
                "count": len(documents),
                "data": documents
            }
        except Exception as e:
            LOGGER.error(f"❌ Error backing up {collection_name}: {e}")
            return {
                "collection": collection_name,
                "count": 0,
                "data": [],
                "error": str(e)
            }
    
    async def create_full_backup(self):
        """Create full database backup"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            backup_data = {
                "timestamp": timestamp,
                "datetime": datetime.now().isoformat(),
                "database": "Character_catcher",
                "collections": {}
            }
            
            # Backup all collections
            collections_to_backup = {
                "anime_characters": collection,
                "user_totals": user_totals_collection,
                "user_collection": user_collection,
                "group_user_totals": group_user_totals_collection,
                "top_global_groups": top_global_groups_collection,
                "pm_users": pm_users,
                "user_balance": user_balance_coll
            }
            
            LOGGER.info("🔄 Starting full database backup...")
            
            for name, coll in collections_to_backup.items():
                backup_data["collections"][name] = await self.backup_collection(coll, name)
            
            # Calculate total documents
            total_docs = sum(
                backup_data["collections"][name]["count"] 
                for name in backup_data["collections"]
            )
            backup_data["total_documents"] = total_docs
            
            # Save to file
            filename = self.backup_dir / f"db_backup_{timestamp}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)
            
            file_size = filename.stat().st_size / 1024  # KB
            LOGGER.info(f"✅ Full backup created: {filename} ({file_size:.2f} KB, {total_docs} documents)")
            
            # Cleanup old backups (keep last 10)
            await self.cleanup_old_backups(keep=10)
            
            return filename
            
        except Exception as e:
            LOGGER.error(f"❌ Backup failed: {e}")
            return None
    
    async def cleanup_old_backups(self, keep: int = 10):
        """Delete old backup files, keeping only the most recent ones"""
        try:
            backup_files = sorted(
                self.backup_dir.glob("db_backup_*.json"),
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            
            if len(backup_files) > keep:
                for old_file in backup_files[keep:]:
                    old_file.unlink()
                    LOGGER.info(f"🗑️ Deleted old backup: {old_file.name}")
                    
        except Exception as e:
            LOGGER.error(f"❌ Error cleaning up backups: {e}")
    
    async def auto_backup_loop(self):
        """Background task for automatic backups"""
        self.is_running = True
        interval_hours = self.backup_interval / 3600
        LOGGER.info(f"🔄 Auto backup system started (interval: {interval_hours} hours)")
        
        while self.is_running:
            try:
                await self.create_full_backup()
                await asyncio.sleep(self.backup_interval)
            except Exception as e:
                LOGGER.error(f"❌ Error in backup loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retry
    
    def start(self):
        """Start auto backup system"""
        if not self.is_running:
            create_background_task(self.auto_backup_loop())
    
    def stop(self):
        """Stop auto backup system"""
        self.is_running = False
        LOGGER.info("🛑 Auto backup system stopped")
    
    async def restore_from_backup(self, backup_file: str):
        """Restore database from backup file (use with caution!)"""
        try:
            with open(backup_file, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            LOGGER.warning(f"⚠️ Starting database restore from {backup_file}")
            
            collections_map = {
                "anime_characters": collection,
                "user_totals": user_totals_collection,
                "user_collection": user_collection,
                "group_user_totals": group_user_totals_collection,
                "top_global_groups": top_global_groups_collection,
                "pm_users": pm_users,
                "user_balance": user_balance_coll
            }
            
            for name, data in backup_data["collections"].items():
                if name in collections_map and "data" in data:
                    coll = collections_map[name]
                    # Clear existing data (WARNING: This deletes all current data!)
                    # await coll.delete_many({})
                    
                    # Insert backup data
                    if data["data"]:
                        await coll.insert_many(data["data"])
                        LOGGER.info(f"✅ Restored {len(data['data'])} documents to {name}")
            
            LOGGER.info("✅ Database restore completed")
            return True
            
        except Exception as e:
            LOGGER.error(f"❌ Restore failed: {e}")
            return False

# Initialize backup system (1 hour interval by default)
db_backup = DatabaseBackup(backup_interval=3600)

# ---------------- BACKGROUND TASK HELPER ---------------- #

def create_background_task(coro):
    """
    Safe background task creator.
    Uses PTB application loop if available,
    otherwise falls back to asyncio.
    """
    try:
        application.create_task(coro)
    except RuntimeError:
        asyncio.create_task(coro)

# ---------------- BACKWARD COMPAT ---------------- #

sudo_users = SUDO_USERS
api_id = API_ID
api_hash = API_HASH
mongo_url = MONGO_URL

# ---------------- EXPORTS ---------------- #

__all__ = [
    "application",
    "create_background_task",
    "collection",
    "db",
    "TOKEN",
    "CHARA_CHANNEL_ID",
    "SUPPORT_CHAT",
    "SUDO_USERS",
    "OWNER_ID",
    "user_balance_coll",
    "change_balance",
    "db_backup",
]