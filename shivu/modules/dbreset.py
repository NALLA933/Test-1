from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient

from shivu import shivuu
from shivu.config import Config


# =========================
# HARD SECURITY
# =========================
OWNER_ID = 7818323042
DB_NAME = "shivu_db"   # apna exact database name yahan rakho


# =========================
# MongoDB client
# =========================
mongo_client = AsyncIOMotorClient(Config.MONGO_URL)


# =========================
# /dbreset (FULL RESET)
# =========================
@shivuu.on_message(filters.command("dbreset"))
async def dbreset(client: Client, message: Message):

    if not message.from_user:
        return

    # 🤫 silent ignore for non-owner
    if message.from_user.id != OWNER_ID:
        return

    try:
        # 💣 DROP COMPLETE DATABASE
        await mongo_client.drop_database(DB_NAME)

        await message.reply_text(
            "DATABASE RESET COMPLETE\n\n"
            f"Dropped database: {DB_NAME}\n"
            "MongoDB will start fresh on next use.",
            parse_mode=None
        )

    except Exception as e:
        await message.reply_text(
            f"Database reset failed:\n{e}",
            parse_mode=None
        )