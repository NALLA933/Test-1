import time
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from shivu import application
from shivu.security import is_owner_or_sudo

async def ping(update: Update, context: CallbackContext) -> None:
    """
    ᴘɪɴɢ ᴄᴏᴍᴍᴀɴᴅ ᴛᴏ ᴄʜᴇᴄᴋ ʙᴏᴛ ʟᴀᴛᴇɴᴄʏ.
    ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴛᴏ sᴜᴅᴏ ᴜsᴇʀs ᴏɴʟʏ.
    """
    user_id = update.effective_user.id
    
    # ᴄʜᴇᴄᴋ ɪғ ᴜsᴇʀ ɪs ᴀᴜᴛʜᴏʀɪᴢᴇᴅ (sᴜᴅᴏ ᴜsᴇʀs ᴏʀ ᴏᴡɴᴇʀ)
    if not is_owner_or_sudo(user_id):
        await update.message.reply_text(
            "⚠️ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪs ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴛᴏ sᴜᴅᴏ ᴜsᴇʀs ᴏɴʟʏ."
        )
        return

    try:
        start_time = time.time()
        message = await update.message.reply_text("🏓 ᴘᴏɴɢ!")
        end_time = time.time()
        
        # ᴄᴀʟᴄᴜʟᴀᴛᴇ ʟᴀᴛᴇɴᴄʏ
        latency = round((end_time - start_time) * 1000, 2)
        
        # ᴇᴅɪᴛ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ ʟᴀᴛᴇɴᴄʏ ɪɴғᴏ
        await message.edit_text(
            f"🏓 **ᴘᴏɴɢ!**\n"
            f"📊 ʟᴀᴛᴇɴᴄʏ: `{latency}ᴍs`\n"
            f"⚡ sᴛᴀᴛᴜs: "
            f"{'ᴇxᴄᴇʟʟᴇɴᴛ' if latency < 100 else 'ɢᴏᴏᴅ' if latency < 300 else 'ғᴀɪʀ'}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ᴇʀʀᴏʀ: {str(e)}")

# ᴀᴅᴅ ʜᴀɴᴅʟᴇʀ
application.add_handler(CommandHandler("ping", ping))
