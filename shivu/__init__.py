import asyncio
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
    
import logging
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

# ---------------- EXPORTS ---------------- #

__all__ = [
    "application",
    "collection",
    "db",
    "TOKEN",
    "CHARA_CHANNEL_ID",
    "SUPPORT_CHAT",
    "SUDO_USERS",
    "OWNER_ID",
    "user_balance_coll",
    "change_balance",
]