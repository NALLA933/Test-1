from pymongo import ReturnDocument
from pyrogram import Client, filters
from pyrogram.types import Message

from shivu import user_totals_collection, shivuu
from shivu.config import Config


# -------------------------
# Owner check helper
# -------------------------
def is_owner(user_id: int) -> bool:
    """
    Check whether the given user_id is the bot owner.
    Config.OWNER_ID is expected to be an int.
    """
    return user_id == Config.OWNER_ID


# =========================
# 1️⃣ /changetime (ALL GROUPS)
# =========================
@shivuu.on_message(filters.command("changetime"))
async def change_time_all_groups(client: Client, message: Message):

    # Safety check
    if not message.from_user:
        return

    # Owner only
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Only Bot Owner can use this command.")
        return

    args = message.command
    if len(args) != 2:
        await message.reply_text(
            "⚠️ **Usage:**\n`/changetime <frequency>`"
        )
        return

    try:
        new_frequency = int(args[1])
    except ValueError:
        await message.reply_text("❌ Frequency must be a number.")
        return

    # Minimum limit for GLOBAL change
    if new_frequency < 50:
        await message.reply_text(
            "⚠️ Frequency must be **>= 50** for global change."
        )
        return

    try:
        result = await user_totals_collection.update_many(
            {},
            {"$set": {"message_frequency": new_frequency}}
        )

        await message.reply_text(
            f"✅ **Global Frequency Updated**\n\n"
            f"⏱ **New Frequency:** `{new_frequency}`\n"
            f"📊 **Groups Updated:** `{result.modified_count}`"
        )

    except Exception as e:
        await message.reply_text(
            f"❌ Failed to update global frequency:\n`{e}`"
        )


# =========================
# 2️⃣ /ctime (SINGLE GROUP)
# =========================
@shivuu.on_message(filters.command("ctime") & filters.group)
async def change_time_single_group(client: Client, message: Message):

    # Safety check
    if not message.from_user:
        return

    # Owner only
    if not is_owner(message.from_user.id):
        await message.reply_text("❌ Only Bot Owner can use this command.")
        return

    args = message.command
    if len(args) != 2:
        await message.reply_text(
            "⚠️ **Usage:**\n`/ctime <frequency>`"
        )
        return

    try:
        new_frequency = int(args[1])
    except ValueError:
        await message.reply_text("❌ Frequency must be a number.")
        return

    chat_id = message.chat.id

    try:
        await user_totals_collection.find_one_and_update(
            {"chat_id": str(chat_id)},
            {"$set": {"message_frequency": new_frequency}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )

        await message.reply_text(
            f"✅ **Group Frequency Updated**\n\n"
            f"👥 **Group:** `{message.chat.title}`\n"
            f"⏱ **New Frequency:** `{new_frequency}`"
        )

    except Exception as e:
        await message.reply_text(
            f"❌ Failed to update group frequency:\n`{e}`"
        )