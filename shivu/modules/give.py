from html import escape
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from shivu import application, user_collection, collection, LOGGER
from shivu.character_ids import character_id_query, normalize_character_document
from shivu.security import is_owner_or_sudo


from shivu.utils import to_small_caps, get_rarity_display

# ---------- Give Command Handler ----------
async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /give <character_id>
    Give a character to a user by replying to their message.
    Admin only command.
    """
    admin_id = update.effective_user.id
    
    # Check if user is admin (Owner or Sudo user)
    if not is_owner_or_sudo(admin_id):
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
    character = await collection.find_one(character_id_query(character_id))
    
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
    character_entry = normalize_character_document({
        "id": character.get("id"),
        "name": character.get("name"),
        "anime": character.get("anime"),
        "rarity": character.get("rarity"),
        "img_url": character.get("img_url")
    })
    
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
