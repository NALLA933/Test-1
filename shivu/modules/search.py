from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
from typing import List, Dict, Optional, Tuple
from cachetools import TTLCache
import asyncio
from functools import wraps

from shivu import shivuu, collection, user_collection

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

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

# TTL Caches (5 minutes TTL for high performance)
character_cache = TTLCache(maxsize=1000, ttl=300)  # Cache character lookups
count_cache = TTLCache(maxsize=500, ttl=300)       # Cache character counts
grabber_cache = TTLCache(maxsize=500, ttl=300)     # Cache top grabbers
search_cache = TTLCache(maxsize=200, ttl=300)      # Cache search results

# ==================== UTILITY FUNCTIONS ====================

def to_small_caps(text):
    """Convert text to small caps for premium UI"""
    text = str(text) if text is not None else 'Unknown'
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)


def cache_key(*args):
    """Generate cache key from arguments"""
    return ':'.join(str(arg) for arg in args)


# ==================== DATABASE ACCESS LAYER ====================

async def get_character_by_id(character_id: str) -> Optional[Dict]:
    """
    Fetch character from database with caching.
    
    Args:
        character_id: The character ID to search for
        
    Returns:
        Character document or None if not found
    """
    key = cache_key('char', character_id)
    
    # Check cache first
    if key in character_cache:
        return character_cache[key]
    
    try:
        character = await collection.find_one({'id': character_id})
        if character:
            character_cache[key] = character
        return character
    except Exception as e:
        logger.error(f"Error fetching character {character_id}: {e}")
        return None


async def get_character_count_optimized(character_id: str) -> int:
    """
    Count how many instances of a character exist across all users using aggregation.
    
    This uses MongoDB aggregation pipeline for optimal performance instead of
    iterating through all documents in Python.
    
    Args:
        character_id: The character ID to count
        
    Returns:
        Total count of this character across all users
    """
    key = cache_key('count', character_id)
    
    # Check cache first
    if key in count_cache:
        return count_cache[key]
    
    try:
        # Use aggregation pipeline for efficient counting
        pipeline = [
            # Match only users who have this character
            {'$match': {'characters.id': character_id}},
            # Unwind the characters array
            {'$unwind': '$characters'},
            # Match only the specific character ID
            {'$match': {'characters.id': character_id}},
            # Count the results
            {'$count': 'total'}
        ]
        
        result = await user_collection.aggregate(pipeline).to_list(length=1)
        count = result[0]['total'] if result else 0
        
        # Cache the result
        count_cache[key] = count
        return count
        
    except Exception as e:
        logger.error(f"Error counting characters {character_id}: {e}")
        return 0


async def get_top_grabbers_optimized(character_id: str, limit: int = 10) -> List[Dict]:
    """
    Get top users who own the most of this character using aggregation.
    
    This uses MongoDB aggregation pipeline for optimal performance.
    
    Args:
        character_id: The character ID to analyze
        limit: Maximum number of top users to return
        
    Returns:
        List of dicts with user_id, username, first_name, count
    """
    key = cache_key('grabbers', character_id, limit)
    
    # Check cache first
    if key in grabber_cache:
        return grabber_cache[key]
    
    try:
        # Use aggregation pipeline for efficient top grabbers query
        pipeline = [
            # Match only users who have this character
            {'$match': {'characters.id': character_id}},
            # Project the necessary fields and count matching characters
            {
                '$project': {
                    'id': 1,
                    'username': 1,
                    'first_name': 1,
                    'count': {
                        '$size': {
                            '$filter': {
                                'input': '$characters',
                                'as': 'char',
                                'cond': {'$eq': ['$$char.id', character_id]}
                            }
                        }
                    }
                }
            },
            # Sort by count descending
            {'$sort': {'count': -1}},
            # Limit to top N
            {'$limit': limit}
        ]
        
        top_users = []
        async for user in user_collection.aggregate(pipeline):
            top_users.append({
                'user_id': user.get('id'),
                'username': user.get('username', 'Unknown'),
                'first_name': user.get('first_name', 'User'),
                'count': user.get('count', 0)
            })
        
        # Cache the result
        grabber_cache[key] = top_users
        return top_users
        
    except Exception as e:
        logger.error(f"Error getting top grabbers for {character_id}: {e}")
        return []


async def search_characters_optimized(search_query: str) -> List[Dict]:
    """
    Search for characters by name with caching.
    
    Args:
        search_query: The search term (case-insensitive)
        
    Returns:
        List of character documents
    """
    # Normalize query for caching
    normalized_query = search_query.lower().strip()
    key = cache_key('search', normalized_query)
    
    # Check cache first
    if key in search_cache:
        return search_cache[key]
    
    try:
        characters = []
        search_regex = {'$regex': search_query, '$options': 'i'}
        
        # Use optimized find with projection to reduce data transfer
        async for char in collection.find(
            {
                '$or': [
                    {'name': search_regex},
                    {'first_name': search_regex},
                    {'last_name': search_regex}
                ]
            },
            {
                'id': 1,
                'name': 1,
                'anime': 1,
                'rarity': 1,
                'img_url': 1,
                '_id': 0
            }
        ):
            characters.append(char)
        
        # Cache the result
        search_cache[key] = characters
        return characters
        
    except Exception as e:
        logger.error(f"Error searching characters for '{search_query}': {e}")
        return []


# ==================== FORMATTING LAYER ====================

def format_character_details(character: Dict, total_count: int, top_grabbers: List[Dict]) -> str:
    """
    Format character details with top grabbers.
    
    Args:
        character: Character document
        total_count: Total instances of this character
        top_grabbers: List of top users
        
    Returns:
        Formatted message string
    """
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

    # Build message with exact same format
    msg = (
        f"📜 ᴄʜᴀʀᴀᴄᴛᴇʀ ɪɴꜰᴏ\n"
        f"🧩 ɴᴀᴍᴇ   : {name_sc}\n"
        f"🧬 ʀᴀʀɪᴛʏ : {rarity_display}\n"
        f"📺 ᴀɴɪᴍᴇ  : {anime_sc}\n"
        f"🆔 ɪᴅ     : {char_id_sc}\n\n"
    )

    # Add global owners
    msg += f"🌍 ɢʟᴏʙᴀʟ ᴏᴡɴᴇʀs\n"

    if top_grabbers:
        for i, grabber in enumerate(top_grabbers, 1):
            first_name = grabber['first_name']
            user_id = grabber['user_id']
            msg += f"{i}. [{to_small_caps(first_name)}](tg://user?id={user_id})\n"
    else:
        msg += f"❌ ɴᴏ ᴜsᴇʀs ᴏᴡɴ ᴛʜɪs ᴄʜᴀʀᴀᴄᴛᴇʀ ʏᴇᴛ\n"

    return msg


def format_sfind_page(characters: List[Dict], page: int, total_pages: int, search_query: str) -> str:
    """
    Format sfind results page.
    
    Args:
        characters: Full list of characters
        page: Current page number (0-indexed)
        total_pages: Total number of pages
        search_query: Original search query
        
    Returns:
        Formatted page message
    """
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


def create_sfind_keyboard(page: int, total_pages: int, user_id: int, search_hash: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for sfind pagination with stateless callback data.
    
    Args:
        page: Current page (0-indexed)
        total_pages: Total number of pages
        user_id: User ID for authorization
        search_hash: Hash of search query for session management
        
    Returns:
        InlineKeyboardMarkup with navigation buttons
    """
    buttons = []
    
    if total_pages > 1:
        buttons.append([
            InlineKeyboardButton(
                to_small_caps("Previous"), 
                callback_data=f"sfind_prev:{user_id}:{page}:{search_hash}"
            ),
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}", 
                callback_data=f"sfind_page:{user_id}:{page}:{search_hash}"
            ),
            InlineKeyboardButton(
                to_small_caps("Next"), 
                callback_data=f"sfind_next:{user_id}:{page}:{search_hash}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            to_small_caps("Close"), 
            callback_data=f"sfind_close:{user_id}"
        )
    ])
    
    return InlineKeyboardMarkup(buttons)


# ==================== MEDIA HANDLING ====================

async def safe_send_media(
    message,
    img_url: Optional[str],
    caption: str,
    reply_markup: InlineKeyboardMarkup
) -> bool:
    """
    Safely send media with fallback to text if media fails.
    
    This prevents bot crashes from invalid/expired URLs.
    
    Args:
        message: Message object to reply to
        img_url: Image URL (can be None or invalid)
        caption: Message caption/text
        reply_markup: Keyboard markup
        
    Returns:
        True if successful, False otherwise
    """
    if not img_url:
        # No image, send text directly
        try:
            await message.reply_text(caption, reply_markup=reply_markup)
            return True
        except Exception as e:
            logger.error(f"Error sending text message: {e}")
            return False
    
    # Try sending with image
    try:
        await message.reply_photo(
            photo=img_url,
            caption=caption,
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        # Image failed, fallback to text
        logger.warning(f"Failed to send image {img_url}: {e}. Falling back to text.")
        try:
            await message.reply_text(caption, reply_markup=reply_markup)
            return True
        except Exception as text_error:
            logger.error(f"Error sending fallback text: {text_error}")
            return False


# ==================== STATELESS PAGINATION HELPERS ====================

def compute_search_hash(search_query: str) -> str:
    """
    Compute a short hash of the search query for callback data.
    
    Args:
        search_query: The search string
        
    Returns:
        8-character hash string
    """
    return str(hash(search_query.lower().strip()) % 100000000).zfill(8)


async def get_cached_search_results(search_hash: str, search_query: str) -> Optional[List[Dict]]:
    """
    Get search results from cache, re-search if not found.
    
    Args:
        search_hash: Hash of the search query
        search_query: Original search query
        
    Returns:
        List of character documents or None if search fails
    """
    normalized_query = search_query.lower().strip()
    key = cache_key('search', normalized_query)
    
    if key in search_cache:
        return search_cache[key]
    
    # Cache miss - re-execute search
    logger.info(f"Cache miss for search '{search_query}', re-executing")
    return await search_characters_optimized(search_query)


# ==================== SCHECK COMMAND ====================

@shivuu.on_message(filters.command(["scheck", "s", "check"]))
async def scheck_command(client, message):
    """Check character info and top grabbers"""
    try:
        # Validate command format
        if len(message.command) != 2:
            await message.reply_text(
                f"❌ **{to_small_caps('invalid format')}!**\n\n"
                f"**{to_small_caps('usage')}:** `/scheck [{to_small_caps('character id')}]`\n"
                f"**{to_small_caps('example')}:** `/scheck 12`"
            )
            return

        character_id = message.command[1]

        # Search for character in database (with caching)
        character = await get_character_by_id(character_id)

        if not character:
            await message.reply_text(
                f"❌ **{to_small_caps('character not found')}!**\n\n"
                f"{to_small_caps('character with id')} `{character_id}` {to_small_caps('is not available in main database')}."
            )
            return

        # Get character stats (optimized with aggregation and caching)
        total_count, top_grabbers = await asyncio.gather(
            get_character_count_optimized(character_id),
            get_top_grabbers_optimized(character_id, limit=10)
        )

        # Format message
        details_msg = format_character_details(character, total_count, top_grabbers)

        # Get character image
        img_url = character.get('img_url')

        # Create keyboard with cancel button
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(to_small_caps("Close"), callback_data=f"scheck_close:{message.from_user.id}")]
        ])

        # Send with safe media handler
        success = await safe_send_media(message, img_url, details_msg, keyboard)
        
        if success:
            logger.info(f"Scheck: User {message.from_user.id} checked character {character_id}")
        else:
            # Last resort error message
            await message.reply_text(
                f"❌ {to_small_caps('an error occurred while processing your request')}. "
                f"{to_small_caps('please try again')}!"
            )

    except Exception as e:
        logger.error(f"Error in scheck command: {e}", exc_info=True)
        await message.reply_text(
            f"❌ {to_small_caps('an error occurred while processing your request')}. "
            f"{to_small_caps('please try again')}!"
        )


@shivuu.on_callback_query(filters.regex(r"^scheck_close:(\d+)$"))
async def scheck_close_callback(client, callback_query):
    """Handle scheck close button"""
    try:
        user_id = int(callback_query.data.split(":")[1])

        # Only allow the user who initiated the command to close
        if callback_query.from_user.id != user_id:
            await callback_query.answer(f"❌ {to_small_caps('this is not for you')}!", show_alert=True)
            return

        await callback_query.message.delete()
        await callback_query.answer(to_small_caps("closed") + "!", show_alert=False)
        
    except Exception as e:
        logger.error(f"Error in scheck_close callback: {e}", exc_info=True)
        await callback_query.answer(f"❌ {to_small_caps('error')}", show_alert=True)


# ==================== SFIND COMMAND ====================

@shivuu.on_message(filters.command(["sfind", "find"]))
async def sfind_command(client, message):
    """Find characters by name"""
    try:
        # Validate command format
        if len(message.command) < 2:
            await message.reply_text(
                f"❌ **{to_small_caps('invalid format')}!**\n\n"
                f"**{to_small_caps('usage')}:** `/sfind [{to_small_caps('character name')}]`\n"
                f"**{to_small_caps('example')}:** `/sfind {to_small_caps('naruto')}`"
            )
            return

        # Get search query (support multiple words)
        search_query = ' '.join(message.command[1:])

        # Search in database (optimized with caching)
        characters = await search_characters_optimized(search_query)

        if not characters:
            await message.reply_text(
                f"❌ **{to_small_caps('no characters found')}!**\n\n"
                f"{to_small_caps('character with name')} **{to_small_caps(search_query)}** "
                f"{to_small_caps('is not available in main database')}."
            )
            return

        # Calculate pagination
        user_id = message.from_user.id
        total_pages = (len(characters) + 9) // 10  # Ceiling division
        page = 0
        search_hash = compute_search_hash(search_query)

        # Format first page
        page_msg = format_sfind_page(characters, page, total_pages, search_query)

        # Create stateless keyboard
        keyboard = create_sfind_keyboard(page, total_pages, user_id, search_hash)

        await message.reply_text(page_msg, reply_markup=keyboard)

        logger.info(f"Sfind: User {user_id} searched for '{search_query}' - found {len(characters)} results")

    except Exception as e:
        logger.error(f"Error in sfind command: {e}", exc_info=True)
        await message.reply_text(
            f"❌ {to_small_caps('an error occurred while processing your request')}. "
            f"{to_small_caps('please try again')}!"
        )


@shivuu.on_callback_query(filters.regex(r"^sfind_(prev|next|close):(\d+)(?::(\d+):(\w+))?$"))
async def sfind_navigation_callback(client, callback_query):
    """
    Handle sfind navigation buttons with stateless pagination.
    
    Callback data format:
    - sfind_close:USER_ID
    - sfind_prev:USER_ID:PAGE:SEARCH_HASH
    - sfind_next:USER_ID:PAGE:SEARCH_HASH
    """
    try:
        data_parts = callback_query.data.split(":")
        action = data_parts[0].split("_")[1]  # prev, next, or close
        user_id = int(data_parts[1])

        # Only allow the user who initiated the command
        if callback_query.from_user.id != user_id:
            await callback_query.answer(f"❌ {to_small_caps('this is not for you')}!", show_alert=True)
            return

        # Handle close
        if action == "close":
            await callback_query.message.delete()
            await callback_query.answer(to_small_caps("closed") + "!", show_alert=False)
            return

        # Parse pagination data
        if len(data_parts) < 4:
            await callback_query.answer(
                f"❌ {to_small_caps('invalid request')}!", 
                show_alert=True
            )
            return

        current_page = int(data_parts[2])
        search_hash = data_parts[3]

        # Extract search query from message (parse from existing message)
        message_text = callback_query.message.text
        query_line = [line for line in message_text.split('\n') if 'query' in line.lower()]
        
        if not query_line:
            await callback_query.answer(
                f"❌ {to_small_caps('session expired')}! {to_small_caps('please search again')}.", 
                show_alert=True
            )
            return

        # Extract query from formatted message (between ** markers)
        try:
            search_query = query_line[0].split('**')[1]
            # Reverse small caps conversion for cache lookup
            search_query_normalized = search_query.lower()
        except (IndexError, AttributeError):
            await callback_query.answer(
                f"❌ {to_small_caps('session expired')}! {to_small_caps('please search again')}.", 
                show_alert=True
            )
            return

        # Get characters from cache
        characters = await get_cached_search_results(search_hash, search_query_normalized)

        if not characters:
            await callback_query.answer(
                f"❌ {to_small_caps('session expired')}! {to_small_caps('please search again')}.", 
                show_alert=True
            )
            return

        total_pages = (len(characters) + 9) // 10

        # Handle navigation
        new_page = current_page
        if action == "prev":
            if current_page > 0:
                new_page = current_page - 1
            else:
                await callback_query.answer(f"❌ {to_small_caps('this is the first page')}!", show_alert=True)
                return
        elif action == "next":
            if current_page < total_pages - 1:
                new_page = current_page + 1
            else:
                await callback_query.answer(f"❌ {to_small_caps('this is the last page')}!", show_alert=True)
                return

        # Format new page
        page_msg = format_sfind_page(characters, new_page, total_pages, search_query)

        # Update keyboard with new page number
        keyboard = create_sfind_keyboard(new_page, total_pages, user_id, search_hash)

        # Update message
        await callback_query.message.edit_text(page_msg, reply_markup=keyboard)
        await callback_query.answer(f"{to_small_caps('page')} {new_page + 1}/{total_pages}", show_alert=False)

    except Exception as e:
        logger.error(f"Error in sfind navigation callback: {e}", exc_info=True)
        await callback_query.answer(
            f"❌ {to_small_caps('error')}. {to_small_caps('please try again')}!", 
            show_alert=True
        )
