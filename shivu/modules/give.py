from html import escape
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from shivu import application, user_collection, collection, LOGGER, OWNER_ID, SUDO_USERS


# ---------- Small Caps Utility ----------
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
    ' ': ' ', ':': ':', '!': '!', '?': '?', '.': '.', ',': ',', '-': '-',
    '(': '(', ')': ')', '[': '[', ']': ']', '{': '{', '}': '}', '=': '=',
    '+': '+', '*': '*', '/': '/', '\\': '\\', '|': '|', '_': '_',
    '0': '0', '1': '1', '2': '2', '3': '3', '4': '4', '5': '5', 
    '6': '6', '7': '7', '8': '8', '9': '9'
}

# ---------- Rarity Mapping ----------
RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ",
    2: "🔵 ʀᴀʀᴇ",
    3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ",
    6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ",
    8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ",
    14: "🍭 ᴋᴀᴡᴀɪɪ",
    15: "🧬 ʜʏʙʀɪᴅ"
}

def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode characters."""
    return ''.join(SMALL_CAPS_MAP.get(char, char) for char in str(text))


def get_rarity_display(rarity: int) -> str:
    """Get rarity display string with emoji and name."""
    return RARITY_MAP.get(rarity, f"⚪ ᴜɴᴋɴᴏᴡɴ ({rarity})")


# ---------- Give Command Handler ----------
async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /give <character_id>
    Give a character to a user by replying to their message.
    Admin only command.
    """
    admin_id = update.effective_user.id
    
    # Check if user is admin (Owner or Sudo user)
    if admin_id != OWNER_ID and admin_id not in SUDO_USERS:
        await update.message.reply_text(
            "❌ " + to_small_caps("You are not authorized to use this command.")
        )
        return
    
    # Check if command is used as a reply
    if not update.message.reply_to_message:
        usage_msg = (
            f"<b>🎁 {to_small_caps('GIVE CHARACTER COMMAND')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')}\n"
            f"   {to_small_caps('Reply to a user message and type:')}\n"
            f"   <code>/give &lt;character_id&gt;</code>\n\n"
            f"💡 {to_small_caps('Example:')}\n"
            f"   {to_small_caps('Reply to user and type:')} <code>/give 123</code>"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return
    
    # Check if character ID is provided
    if len(context.args) < 1:
        await update.message.reply_text(
            f"❌ {to_small_caps('Please provide a character ID.')}\n"
            f"📝 {to_small_caps('Usage:')} <code>/give &lt;character_id&gt;</code>",
            parse_mode="HTML"
        )
        return
    
    # Get target user ID from replied message
    target_user = update.message.reply_to_message.from_user
    target_user_id = target_user.id
    target_user_name = target_user.first_name
    
    # Parse character ID
    try:
        character_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            f"❌ {to_small_caps('Invalid character ID. Must be a number.')}"
        )
        return
    
    if character_id <= 0:
        await update.message.reply_text(
            "❌ " + to_small_caps("Character ID must be greater than 0.")
        )
        return
    
    # Fetch character from database
    character = await collection.find_one({"id": character_id})
    
    # If not found with integer, try string
    if not character:
        character = await collection.find_one({"id": str(character_id)})
    
    if not character:
        error_msg = (
            f"❌ {to_small_caps('Character Not Found')}\n\n"
            f"🔍 {to_small_caps(f'The character with ID {character_id} does not exist in the database.')}\n"
            f"💡 {to_small_caps('Please verify the character ID and try again.')}"
        )
        await update.message.reply_text(error_msg, parse_mode="HTML")
        return
    
    # Extract character data
    character_name = character.get("name", "Unknown")
    anime_name = character.get("anime", "Unknown")
    rarity = character.get("rarity", 1)
    img_url = character.get("img_url", "")
    rarity_display = get_rarity_display(rarity)
    
    # Prepare character entry
    character_entry = {
        "id": character.get("id"),
        "name": character.get("name"),
        "anime": character.get("anime"),
        "rarity": character.get("rarity"),
        "img_url": character.get("img_url")
    }
    
    # Add optional fields if they exist
    optional_fields = ["id_al", "video_url"]
    for field in optional_fields:
        if field in character:
            character_entry[field] = character.get(field)
    
    # Add character to user's collection
    try:
        await user_collection.update_one(
            {"id": target_user_id},
            {
                "$push": {"characters": character_entry},
                "$setOnInsert": {
                    "id": target_user_id,
                    "first_name": target_user_name,
                    "balance": 0,
                    "favorites": []
                }
            },
            upsert=True
        )
        
        LOGGER.info(f"Admin {admin_id} gave character {character_id} ({character_name}) to user {target_user_id}")
        
        # Success message with character image
        success_msg = (
            f"<b>✅ {to_small_caps('CHARACTER GIVEN SUCCESSFULLY!')}</b>\n\n"
            f"👤 <b>{to_small_caps('To:')}</b> {escape(target_user_name)}\n"
            f"🎴 <b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
            f"📺 <b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
            f"🆔 <b>{to_small_caps('ID:')}</b> {character_id}\n"
            f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}"
        )
        
        # Try to send with image
        if img_url:
            try:
                await update.message.reply_photo(
                    photo=img_url,
                    caption=success_msg,
                    parse_mode="HTML"
                )
            except Exception as e:
                LOGGER.error(f"Failed to send image: {e}")
                # Fallback to text message
                await update.message.reply_text(success_msg, parse_mode="HTML")
        else:
            await update.message.reply_text(success_msg, parse_mode="HTML")
            
    except Exception as e:
        LOGGER.error(f"Failed to give character {character_id} to user {target_user_id}: {e}")
        await update.message.reply_text(
            f"❌ {to_small_caps('Failed to give character. Database error.')}\n"
            f"ℹ️ {to_small_caps('Please try again later.')}",
            parse_mode="HTML"
        )


# ---------- Handler Registration ----------
def register_handlers():
    """Register give command handler with the application."""
    application.add_handler(CommandHandler("give", give_command, block=False))
    LOGGER.info("Give command handler registered successfully")


# Auto-register handlers when module is imported
register_handlers()
