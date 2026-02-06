from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from html import escape
import math
import asyncio
import functools
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import hashlib

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from shivu import collection, user_collection, application

# ============= CONFIGURATION =============
CACHE_TTL = 300
PAGE_SIZE = 15

# Redis Client Setup
redis_client = None
if REDIS_AVAILABLE:
    try:
        redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    except:
        pass

# ============= ULTRA-FAST SMALL CAPS =============
_SMALL_CAPS_MAP = str.maketrans({
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
    'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
    'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
    'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ',
    'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
    'z': 'ᴢ', 'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ',
    'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ',
    'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ',
    'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
    'Y': 'ʏ', 'Z': 'ᴢ'
})

def to_small_caps(text: str) -> str:
    """Ultra-fast translation using str.translate()"""
    if not text:
        return ""
    return str(text).translate(_SMALL_CAPS_MAP)

# ============= RARITY CONFIG =============
RARITY_EMOJIS = {
    1: '⚪', 2: '🔵', 3: '🟡', 4: '💮', 5: '👹',
    6: '🎐', 7: '🔮', 8: '🪐', 9: '⚰️', 10: '🌬️',
    11: '💝', 12: '🌸', 13: '🏖️', 14: '🍭', 15: '🧬'
}

# ============= SMART CACHE DECORATOR =============
def cached(ttl_seconds: int = CACHE_TTL):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not redis_client:
                return await func(*args, **kwargs)
            
            key_parts = [func.__name__] + [str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()]
            cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()
            
            try:
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    import json
                    return json.loads(cached_data)
            except:
                pass
            
            result = await func(*args, **kwargs)
            
            try:
                if result is not None:
                    import json
                    await redis_client.setex(cache_key, ttl_seconds, json.dumps(result, default=str))
            except:
                pass
            
            return result
        return wrapper
    return decorator

# ============= HIGH-PERFORMANCE HAREM MANAGER =============
class HaremManagerV3:
    
    @staticmethod
    @cached(ttl_seconds=60)
    async def get_user_characters_fast(user_id: int, rarity_filter: Optional[int] = None) -> Tuple[Optional[dict], List[dict]]:
        pipeline = [
            {"$match": {"id": user_id}},
            {"$project": {
                "characters": 1,
                "favorites": 1,
                "name": 1,
                "_id": 0
            }}
        ]
        
        user_data = await user_collection.aggregate(pipeline).to_list(1)
        if not user_data:
            return None, []
        
        user = user_data[0]
        characters = user.get('characters', [])
        
        if not characters:
            return user, []
        
        if rarity_filter is not None:
            characters = [c for c in characters if c.get('rarity') == rarity_filter]
        
        return user, characters
    
    @staticmethod
    async def get_character_details_batch(char_ids: List[str]) -> Dict[str, dict]:
        """🔧 FIX: Explicitly include 'rarity' field in projection"""
        if not char_ids:
            return {}
        
        unique_ids = list(set(char_ids))
        
        # 🔧 FIX: Ensure 'rarity' is included in projection
        projection = {
            "id": 1, 
            "name": 1, 
            "anime": 1, 
            "rarity": 1,  # ✅ Yeh ensure karta hai ki rarity aaye
            "img_url": 1, 
            "_id": 0
        }
        
        cursor = collection.find(
            {"id": {"$in": unique_ids}},
            projection
        )
        
        char_map = {}
        async for char in cursor:
            char_map[char['id']] = char
        
        return char_map
    
    @staticmethod
    async def get_anime_counts_batch(animes: List[str]) -> Dict[str, int]:
        if not animes:
            return {}
        
        pipeline = [
            {"$match": {"anime": {"$in": animes}}},
            {"$group": {"_id": "$anime", "count": {"$sum": 1}}}
        ]
        
        results = {}
        async for doc in collection.aggregate(pipeline):
            results[doc['_id']] = doc['count']
        
        return results

# ============= MAIN HANDLER (V3) =============
async def harem_v3(update: Update, context: CallbackContext, page: int = 0) -> None:
    user_id = update.effective_user.id
    
    rarity_filter = None
    try:
        from shivu.modules.smode import get_user_sort_preference, RARITY_OPTIONS
        rarity_filter = await get_user_sort_preference(user_id)
    except:
        RARITY_OPTIONS = {}
    
    # Step 1: Get user data
    user, user_chars = await HaremManagerV3.get_user_characters_fast(user_id, rarity_filter)
    
    if not user:
        msg = to_small_caps("You Have Not Guessed any Characters Yet..")
        await _send_message(update, msg)
        return
    
    total_count = len(user_chars)
    
    if not user_chars:
        if rarity_filter:
            msg = to_small_caps(f"No Characters Of This Rarity! Use /smode")
        else:
            msg = to_small_caps("You Have Not Guessed any Characters Yet..")
        await _send_message(update, msg)
        return
    
    # Step 2: Process characters
    char_id_counts = {}
    unique_char_ids = []
    seen = set()
    
    for char in user_chars:
        cid = char.get('id')
        if cid:
            char_id_counts[cid] = char_id_counts.get(cid, 0) + 1
            if cid not in seen:
                seen.add(cid)
                unique_char_ids.append(cid)
    
    # Pagination logic
    total_unique = len(unique_char_ids)
    total_pages = max(1, math.ceil(total_unique / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_ids = unique_char_ids[start_idx:end_idx]
    
    # Step 3: 🔧 Fetch character details (ab rarity ke saath)
    char_details = await HaremManagerV3.get_character_details_batch(page_ids)
    
    # Build display list
    display_chars = []
    for cid in page_ids:
        if cid in char_details:
            char_data = char_details[cid]
            char_data['count'] = char_id_counts[cid]
            display_chars.append(char_data)
    
    display_chars.sort(key=lambda x: x.get('anime', ''))
    
    # Step 4: Get anime counts
    page_animes = list({c.get('anime') for c in display_chars})
    anime_counts_task = asyncio.create_task(
        HaremManagerV3.get_anime_counts_batch(page_animes)
    )
    
    # Build message
    safe_name = escape(update.effective_user.first_name)
    header = f"<b>{to_small_caps(f'{safe_name} S HAREM - PAGE {page+1}/{total_pages}')}</b>\n"
    
    if rarity_filter:
        header += f"<b>{to_small_caps(f'FILTER: {rarity_filter} ({total_count})')}</b>\n"
    
    harem_msg = header + "\n"
    
    # Group by anime
    from itertools import groupby
    grouped = {k: list(v) for k, v in groupby(display_chars, key=lambda x: x.get('anime', 'Unknown'))}
    anime_counts = await anime_counts_task
    
    for anime, chars in grouped.items():
        safe_anime = escape(str(anime))
        total_in_anime = anime_counts.get(anime, len(chars))
        
        harem_msg += f"<b>𖤍 {to_small_caps(safe_anime)} {{{len(chars)}/{total_in_anime}}}</b>\n"
        harem_msg += f"{to_small_caps('--------------------')}\n"
        
        for char in chars:
            name = to_small_caps(escape(char.get('name', 'Unknown')))
            
            # 🔧 FIX: Rarity emoji fetch with fallback
            rarity = char.get('rarity', 1)  # Default 1 (Common) agar nahi mila
            emoji = RARITY_EMOJIS.get(rarity, '⚪')
            
            count = char.get('count', 1)
            
            # 🔧 FORMAT: [emoji] ke andar correct rarity emoji
            harem_msg += f"✶ {char['id']} [{emoji}] {name} x{count}\n"
        
        harem_msg += f"{to_small_caps('--------------------')}\n\n"
    
    # Build keyboard
    keyboard = []
    keyboard.append([
        InlineKeyboardButton(
            to_small_caps(f"🔮 See Collection ({total_count})"),
            switch_inline_query_current_chat=f"collection.{user_id}"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            "❌ " + to_small_caps("Cancel"),
            callback_data=f"open_smode:{user_id}"
        )
    ])
    
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{page-1}:{user_id}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"harem:{page+1}:{user_id}"))
        keyboard.append(nav_buttons)
    
    markup = InlineKeyboardMarkup(keyboard)
    
    # Get photo
    photo_url = None
    if user.get('favorites'):
        fav_id = user['favorites'][0]
        if fav_id in char_details:
            photo_url = char_details[fav_id].get('img_url')
    
    if not photo_url and display_chars:
        photo_url = display_chars[0].get('img_url')
    
    # Send
    try:
        if photo_url:
            if update.message:
                await update.message.reply_photo(photo_url, caption=harem_msg, reply_markup=markup, parse_mode='HTML')
            else:
                await update.callback_query.edit_message_caption(caption=harem_msg, reply_markup=markup, parse_mode='HTML')
        else:
            await _send_message(update, harem_msg, markup)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise

async def _send_message(update: Update, text: str, markup=None):
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
        except:
            pass

# ============= CALLBACK HANDLER =============
async def harem_callback_v3(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    data = query.data
    
    try:
        _, page, user_id = data.split(':')
        page, user_id = int(page), int(user_id)
    except:
        await query.answer(to_small_caps("Invalid"), show_alert=True)
        return
    
    if query.from_user.id != user_id:
        await query.answer(to_small_caps("Not Your Harem"), show_alert=True)
        return
    
    await query.answer()
    await harem_v3(update, context, page)

# ============= REGISTRATION =============
application.add_handler(CommandHandler(["harem", "collection"], harem_v3, block=False))
application.add_handler(CallbackQueryHandler(harem_callback_v3, pattern=r'^harem:', block=False))
