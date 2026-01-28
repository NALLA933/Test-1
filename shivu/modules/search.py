from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging

from shivu import shivuu, collection, user_collection

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rarity mapping with small caps
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

# Small caps conversion map
SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
    'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
    'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
    'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
    'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
    'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
    'Y': 'ʏ', 'Z': 'ᴢ'
}

def to_small_caps(text):
    """Convert text to small caps for premium UI"""
    text = str(text) if text is not None else 'Unknown'
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

# Storage for sfind pagination
sfind_sessions = {}  # {user_id: {'characters': [...], 'page': 0}}


async def get_character_count(character_id):
    """Count how many users have this character"""
    try:
        # Count users who have this character ID in their collection
        count = 0
        async for user in user_collection.find({'characters.id': character_id}):
            # Count how many times this character appears in user's collection
            user_chars = user.get('characters', [])
            for char in user_chars:
                if char.get('id') == character_id:
                    count += 1
        return count
    except Exception as e:
        logger.error(f"Error counting characters: {e}")
        return 0


async def get_top_grabbers(character_id, limit=10):
    """Get top 10 users who have the most of this character"""
    try:
        top_users = []
        
        # Find all users who have this character
        async for user in user_collection.find({'characters.id': character_id}):
            user_id = user.get('id')
            username = user.get('username', 'Unknown')
            first_name = user.get('first_name', 'User')
            
            # Count how many times this character appears
            char_count = sum(1 for char in user.get('characters', []) if char.get('id') == character_id)
            
            if char_count > 0:
                top_users.append({
                    'user_id': user_id,
                    'username': username,
                    'first_name': first_name,
                    'count': char_count
                })
        
        # Sort by count and get top 10
        top_users.sort(key=lambda x: x['count'], reverse=True)
        return top_users[:limit]
        
    except Exception as e:
        logger.error(f"Error getting top grabbers: {e}")
        return []


def format_character_details(character, total_count, top_grabbers):
    """Format character details with top grabbers"""
    name = character.get('name', 'Unknown')
    anime = character.get('anime', 'Unknown')
    char_id = character.get('id', 'Unknown')
    rarity = character.get('rarity', 'Unknown')
    
    # Get rarity display
    if isinstance(rarity, int) and rarity in RARITY_MAP:
        rarity_display = RARITY_MAP[rarity]
    else:
        rarity_display = to_small_caps(str(rarity))
    
    # Convert to small caps
    name_sc = to_small_caps(name)
    anime_sc = to_small_caps(anime)
    char_id_sc = to_small_caps(char_id)
    
    # Build message
    msg = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 {to_small_caps('character info')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✨ {to_small_caps('name')}   : **{name_sc}**\n"
        f"🎬 {to_small_caps('anime')}  : **{anime_sc}**\n"
        f"🆔 {to_small_caps('id')}     : `{char_id_sc}`\n"
        f"⭐ {to_small_caps('rarity')} : {rarity_display}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 {to_small_caps('total grabbed')} : **{total_count}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )
    
    # Add top grabbers
    if top_grabbers:
        msg += f"🏆 {to_small_caps('top 10 grabbers')}:\n\n"
        for i, grabber in enumerate(top_grabbers, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            username = grabber['username'] if grabber['username'] else grabber['first_name']
            msg += f"{medal} [{username}](tg://user?id={grabber['user_id']}) - **{grabber['count']}x**\n"
    else:
        msg += f"❌ {to_small_caps('no grabbers yet')}\n"
    
    msg += f"━━━━━━━━━━━━━━━━━━"
    
    return msg


def format_sfind_page(characters, page, total_pages, search_query):
    """Format sfind results page"""
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(characters))
    page_chars = characters[start_idx:end_idx]
    
    msg = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔍 {to_small_caps('search results')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔎 {to_small_caps('query')}: **{to_small_caps(search_query)}**\n"
        f"📄 {to_small_caps('page')}: **{page + 1}/{total_pages}**\n"
        f"📊 {to_small_caps('total found')}: **{len(characters)}**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, char in enumerate(page_chars, start=start_idx + 1):
        char_id = char.get('id', 'Unknown')
        name = char.get('name', 'Unknown')
        anime = char.get('anime', 'Unknown')
        rarity = char.get('rarity', 'Unknown')
        
        # Get rarity display
        if isinstance(rarity, int) and rarity in RARITY_MAP:
            rarity_display = RARITY_MAP[rarity]
        else:
            rarity_display = to_small_caps(str(rarity))
        
        msg += (
            f"**{i}.** {to_small_caps(name)}\n"
            f"   {to_small_caps('anime')}: {to_small_caps(anime)}\n"
            f"   {to_small_caps('id')}: `{char_id}`\n"
            f"   {to_small_caps('rarity')}: {rarity_display}\n\n"
        )
    
    msg += f"━━━━━━━━━━━━━━━━━━"
    return msg


# ==================== SCHECK COMMAND ====================

@shivuu.on_message(filters.command(["scheck", "s", "check"]))
async def scheck_command(client, message):
    """Check character info and top grabbers"""
    try:
        # Validate command format
        if len(message.command) != 2:
            await message.reply_text(
                "❌ **Invalid Format!**\n\n"
                "**Usage:** `/scheck [Character ID]`\n"
                "**Example:** `/scheck 12`"
            )
            return
        
        character_id = message.command[1]
        
        # Search for character in database
        character = await collection.find_one({'id': character_id})
        
        if not character:
            await message.reply_text(
                f"❌ **Character not found!**\n\n"
                f"Character with ID `{character_id}` is not available in main database."
            )
            return
        
        # Get character stats
        total_count = await get_character_count(character_id)
        top_grabbers = await get_top_grabbers(character_id, limit=10)
        
        # Format message
        details_msg = format_character_details(character, total_count, top_grabbers)
        
        # Get character image
        img_url = character.get('img_url')
        
        # Create keyboard with cancel button (small caps, no emoji)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(to_small_caps("Close"), callback_data=f"scheck_close:{message.from_user.id}")]
        ])
        
        # Send with image if available
        if img_url:
            await message.reply_photo(
                photo=img_url,
                caption=details_msg,
                reply_markup=keyboard
            )
        else:
            await message.reply_text(
                details_msg,
                reply_markup=keyboard
            )
        
        logger.info(f"Scheck: User {message.from_user.id} checked character {character_id}")
        
    except Exception as e:
        logger.error(f"Error in scheck command: {e}")
        await message.reply_text("❌ An error occurred while processing your request. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^scheck_close:(\d+)$"))
async def scheck_close_callback(client, callback_query):
    """Handle scheck close button"""
    user_id = int(callback_query.data.split(":")[1])
    
    # Only allow the user who initiated the command to close
    if callback_query.from_user.id != user_id:
        await callback_query.answer("❌ This is not for you!", show_alert=True)
        return
    
    await callback_query.message.delete()
    await callback_query.answer("Closed!", show_alert=False)


# ==================== SFIND COMMAND ====================

@shivuu.on_message(filters.command(["sfind", "find"]))
async def sfind_command(client, message):
    """Find characters by name"""
    try:
        # Validate command format
        if len(message.command) < 2:
            await message.reply_text(
                "❌ **Invalid Format!**\n\n"
                "**Usage:** `/sfind [Character Name]`\n"
                "**Example:** `/sfind Naruto`"
            )
            return
        
        # Get search query (support multiple words)
        search_query = ' '.join(message.command[1:])
        
        # Search in database (case-insensitive search for first_name, last_name, or name)
        characters = []
        search_regex = {'$regex': search_query, '$options': 'i'}
        
        async for char in collection.find({
            '$or': [
                {'name': search_regex},
                {'first_name': search_regex},
                {'last_name': search_regex}
            ]
        }):
            characters.append(char)
        
        if not characters:
            await message.reply_text(
                f"❌ **No characters found!**\n\n"
                f"Character with name **{search_query}** is not available in main database."
            )
            return
        
        # Store session
        user_id = message.from_user.id
        sfind_sessions[user_id] = {
            'characters': characters,
            'page': 0,
            'search_query': search_query
        }
        
        # Calculate total pages
        total_pages = (len(characters) + 9) // 10  # Ceiling division
        
        # Format first page
        page_msg = format_sfind_page(characters, 0, total_pages, search_query)
        
        # Create keyboard (small caps, no emojis)
        buttons = []
        if total_pages > 1:
            buttons.append([
                InlineKeyboardButton(to_small_caps("Previous"), callback_data=f"sfind_prev:{user_id}"),
                InlineKeyboardButton(f"{1}/{total_pages}", callback_data=f"sfind_page:{user_id}"),
                InlineKeyboardButton(to_small_caps("Next"), callback_data=f"sfind_next:{user_id}")
            ])
        buttons.append([
            InlineKeyboardButton(to_small_caps("Close"), callback_data=f"sfind_close:{user_id}")
        ])
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        await message.reply_text(
            page_msg,
            reply_markup=keyboard
        )
        
        logger.info(f"Sfind: User {user_id} searched for '{search_query}' - found {len(characters)} results")
        
    except Exception as e:
        logger.error(f"Error in sfind command: {e}")
        await message.reply_text("❌ An error occurred while processing your request. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^sfind_(prev|next|close):(\d+)$"))
async def sfind_navigation_callback(client, callback_query):
    """Handle sfind navigation buttons"""
    data_parts = callback_query.data.split(":")
    action = data_parts[0].split("_")[1]  # prev, next, or close
    user_id = int(data_parts[1])
    
    # Only allow the user who initiated the command
    if callback_query.from_user.id != user_id:
        await callback_query.answer("❌ This is not for you!", show_alert=True)
        return
    
    # Handle close
    if action == "close":
        if user_id in sfind_sessions:
            del sfind_sessions[user_id]
        await callback_query.message.delete()
        await callback_query.answer("Closed!", show_alert=False)
        return
    
    # Check if session exists
    if user_id not in sfind_sessions:
        await callback_query.answer("❌ Session expired! Please search again.", show_alert=True)
        return
    
    session = sfind_sessions[user_id]
    characters = session['characters']
    current_page = session['page']
    search_query = session['search_query']
    total_pages = (len(characters) + 9) // 10
    
    # Handle navigation
    if action == "prev":
        if current_page > 0:
            session['page'] -= 1
        else:
            await callback_query.answer("❌ This is the first page!", show_alert=True)
            return
    elif action == "next":
        if current_page < total_pages - 1:
            session['page'] += 1
        else:
            await callback_query.answer("❌ This is the last page!", show_alert=True)
            return
    
    # Format new page
    new_page = session['page']
    page_msg = format_sfind_page(characters, new_page, total_pages, search_query)
    
    # Update keyboard (small caps, no emojis)
    buttons = []
    if total_pages > 1:
        buttons.append([
            InlineKeyboardButton(to_small_caps("Previous"), callback_data=f"sfind_prev:{user_id}"),
            InlineKeyboardButton(f"{new_page + 1}/{total_pages}", callback_data=f"sfind_page:{user_id}"),
            InlineKeyboardButton(to_small_caps("Next"), callback_data=f"sfind_next:{user_id}")
        ])
    buttons.append([
        InlineKeyboardButton(to_small_caps("Close"), callback_data=f"sfind_close:{user_id}")
    ])
    
    keyboard = InlineKeyboardMarkup(buttons)
    
    # Update message
    await callback_query.message.edit_text(
        page_msg,
        reply_markup=keyboard
    )
    await callback_query.answer(f"Page {new_page + 1}/{total_pages}", show_alert=False)
