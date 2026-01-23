from pymongo import ReturnDocument
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Message

from shivu import user_totals_collection, shivuu

# Allowed admin roles
ADMINS = [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]


@shivuu.on_message(filters.command("ctime") & filters.group)
async def change_group_time(client: Client, message: Message):

    # Safety: message must have a sender
    if not message.from_user:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    # Admin check
    try:
        member = await shivuu.get_chat_member(chat_id, user_id)
    except Exception:
        await message.reply_text("❌ Unable to verify admin status.")
        return

    if member.status not in ADMINS:
        await message.reply_text("❌ You are not an Admin.")
        return

    # Command args
    args = message.command
    if len(args) != 2:
        await message.reply_text(
            "⚠️ **Usage:**\n`/ctime <frequency>`"
        )
        return

    # Validate frequency
    try:
        new_frequency = int(args[1])
    except ValueError:
        await message.reply_text("❌ Frequency must be a number.")
        return

    if new_frequency < 100:
        await message.reply_text(
            "⚠️ Frequency must be **greater than or equal to 100**."
        )
        return

    # Update DB
    try:
        await user_totals_collection.find_one_and_update(
            {"chat_id": str(chat_id)},
            {"$set": {"message_frequency": new_frequency}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )

        await message.reply_text(
            f"✅ **Frequency Updated Successfully**\n\n"
            f"👥 **Group:** `{message.chat.title}`\n"
            f"⏱ **New Frequency:** `{new_frequency}`"
        )

    except Exception as e:
        await message.reply_text(f"❌ Failed to update:\n`{e}`")