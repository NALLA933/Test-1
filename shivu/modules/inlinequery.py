import re
import time
import asyncio
from html import escape
from functools import lru_cache
from cachetools import TTLCache
from pymongo import ASCENDING, DESCENDING
from motor.motor_asyncio import AsyncIOMotorClient
import logging

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext
from telegram.error import BadRequest

from shivu import user_collection, collection, application, db

# --- Configuration ---
CACHE_TTL_CHARS = 120      # 2 min global characters
CACHE_TTL_USER = 5         # 5 sec user data
CACHE_TTL_COUNT = 60       # 1 min for count aggregates
MAX_RESULTS = 50
BATCH_SIZE = 10           # For parallel processing

# --- Pre-compiled Regex Cache ---
_regex_cache = {}

def get_regex(pattern: str):
    """Thread-safe compiled regex cache"""
    if pattern not in _regex_cache:
        _regex_cache[pattern] = re.compile(re.escape(pattern), re.IGNORECASE)
    return _regex_cache[pattern]

# --- Rarity Mapping ---
RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ", 2: "🔵 ʀᴀʀᴇ", 3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ", 4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ", 6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ", 7: "🔮 ᴇᴘɪᴄ", 8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ", 10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ", 11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ", 13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ", 14: "🍭 ᴋᴀᴡᴀɪɪ", 15: "🧬 ʜʏʙʀɪᴅ"
}

# --- Optimized Small Caps (Using str.translate) ---
_SMALL_CAPS_TRANS = str.maketrans(
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
    'ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ'
)

def to_small_caps(text: str) -> str:
    """O(n) optimized small caps using C-level translate"""
    if not text:
        return ""
    return str(text).translate(_SMALL_CAPS_TRANS)

# --- Smart Caching with Async Lock ---
class AsyncCache:
    def __init__(self, ttl: int):
        self.cache = TTLCache(maxsize=10000, ttl=ttl)
        self.locks = {}
    
    async def get(self, key: str, fetch_func):
        """Prevents cache stampede with per-key locks"""
        if key in self.cache:
            return self.cache[key]
        
        # Create lock for this key if not exists
        if key not in self.locks:
            self.locks[key] = asyncio.Lock()
        
        async with self.locks[key]:
            # Double-check after acquiring lock
            if key in self.cache:
                return self.cache[key]
            
            value = await fetch_func()
            self.cache[key] = value
            return value

# Initialize caches
char_cache = AsyncCache(CACHE_TTL_CHARS)
user_cache = AsyncCache(CACHE_TTL_USER)
count_cache = AsyncCache(CACHE_TTL_COUNT)

# --- Database Indexes (Run once at startup) ---
async def setup_indexes():
    """Idempotent index creation"""
    await db.characters.create_index([('id', ASCENDING)], unique=True)
    await db.characters.create_index([('anime', ASCENDING)])
    await db.characters.create_index([('name', ASCENDING)])  # Added for text search
    await db.characters.create_index([('rarity', ASCENDING)])
    
    await db.user_collection.create_index([('id', ASCENDING)], unique=True)
    await db.user_collection.create_index([('characters.id', ASCENDING)])
    await db.user_collection.create_index([('characters.anime', ASCENDING)])

# --- Batch Aggregate Queries (Fixes N+1) ---
async def get_character_stats(character_ids: list, anime_names: list):
    """Single aggregation for all counts"""
    pipeline = [
        {
            '$facet': {
                'global_counts': [
                    {'$match': {'characters.id': {'$in': character_ids}}},
                    {'$unwind': '$characters'},
                    {'$match': {'characters.id': {'$in': character_ids}}},
                    {'$group': {'_id': '$characters.id', 'count': {'$sum': 1}}}
                ],
                'anime_counts': [
                    {'$match': {'characters.anime': {'$in': anime_names}}},
                    {'$unwind': '$characters'},
                    {'$match': {'characters.anime': {'$in': anime_names}}},
                    {'$group': {'_id': '$characters.anime', 'count': {'$sum': 1}}}
                ]
            }
        }
    ]
    
    result = await user_collection.aggregate(pipeline).to_list(length=1)
    if not result:
        return {}, {}
    
    global_map = {item['_id']: item['count'] for item in result[0]['global_counts']}
    anime_map = {item['_id']: item['count'] for item in result[0]['anime_counts']}
    
    return global_map, anime_map

async def get_anime_totals(anime_names: list):
    """Batch count anime totals from characters collection"""
    pipeline = [
        {'$match': {'anime': {'$in': anime_names}}},
        {'$group': {'_id': '$anime', 'count': {'$sum': 1}}}
    ]
    result = await collection.aggregate(pipeline).to_list(length=None)
    return {item['_id']: item['count'] for item in result}

# --- Main Handler with Optimizations ---
async def inlinequery(update: Update, context: CallbackContext) -> None:
    start_time = time.time()
    query = update.inline_query.query or ""
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    
    try:
        # Parse query format
        is_collection_query = False
        user_id = None
        search_terms = query
        
        if query.startswith('collection.'):
            parts = query.split(' ', 1)
            user_id = int(parts[0].split('.')[1])
            search_terms = parts[1] if len(parts) > 1 else ""
            is_collection_query = True
        elif query and query.split()[0].isdigit():
            parts = query.split(' ', 1)
            user_id = int(parts[0])
            search_terms = parts[1] if len(parts) > 1 else ""
            is_collection_query = True
        
        # Fetch characters based on query type
        if is_collection_query:
            # Optimized user fetch with projection
            user = await user_cache.get(
                f"user_{user_id}",
                lambda: user_collection.find_one(
                    {'id': user_id},
                    {'characters': 1, 'first_name': 1, 'id': 1}
                )
            )
            
            if not user or 'characters' not in user:
                await update.inline_query.answer([], cache_time=0)
                return
            
            # Efficient deduplication using seen set
            seen_ids = set()
            all_characters = []
            char_count_map = {}  # Track user character counts
            
            for char in user['characters']:
                cid = char['id']
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    all_characters.append(char)
                    char_count_map[cid] = 1
                else:
                    char_count_map[cid] += 1
            
            # Apply search filter if exists
            if search_terms:
                regex = get_regex(search_terms)
                all_characters = [
                    c for c in all_characters 
                    if regex.search(c.get('name', '')) or regex.search(c.get('anime', ''))
                ]
        
        else:
            # Global character search
            if search_terms:
                regex = get_regex(search_terms)
                all_characters = await collection.find(
                    {"$or": [{"name": regex}, {"anime": regex}]},
                    {'id': 1, 'name': 1, 'anime': 1, 'img_url': 1, 'rarity': 1}
                ).to_list(length=None)
            else:
                # Cached global list
                async def fetch_all():
                    return await collection.find(
                        {},
                        {'id': 1, 'name': 1, 'anime': 1, 'img_url': 1, 'rarity': 1}
                    ).to_list(length=None)
                
                all_characters = await char_cache.get('all_chars', fetch_all)
        
        # Pagination
        total_count = len(all_characters)
        characters = all_characters[offset:offset + MAX_RESULTS]
        next_offset = str(offset + MAX_RESULTS) if total_count > offset + MAX_RESULTS else ""
        
        if not characters:
            await update.inline_query.answer([], next_offset=next_offset, cache_time=0)
            return
        
        # Pre-fetch all stats in 2 queries (Fixes N+1)
        char_ids = [c['id'] for c in characters]
        anime_names = list(set(c['anime'] for c in characters if c.get('anime')))
        
        # Parallel stats fetching
        global_counts, user_anime_counts = await get_character_stats(char_ids, anime_names)
        anime_totals = await get_anime_totals(anime_names)
        
        # Build results
        results = []
        for char in characters:
            rarity_val = char.get('rarity')
            rarity_display = RARITY_MAP.get(int(rarity_val), to_small_caps(str(rarity_val))) if rarity_val else to_small_caps("ɴ/ᴀ")
            
            if is_collection_query:
                user_char_count = char_count_map.get(char['id'], 1)
                user_anime_count = sum(
                    1 for c in user['characters'] 
                    if c.get('anime') == char.get('anime')
                )
                
                caption = (
                    f"✨ {to_small_caps('look at')} {to_small_caps(user.get('first_name', 'user'))}'s {to_small_caps('character')}\n\n"
                    f"🌸 {to_small_caps('name')} : <b>{to_small_caps(char['name'])} (x{user_char_count})</b>\n"
                    f"🏖️ {to_small_caps('anime')} : <b>{to_small_caps(char['anime'])} ({user_anime_count}/{anime_totals.get(char['anime'], '?')})</b>\n"
                    f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
                    f"🆔️ {to_small_caps('id')} : <b>{char['id']}</b>"
                )
            else:
                g_count = global_counts.get(char['id'], 0)
                caption = (
                    f"✨ {to_small_caps('look at this character !!')}\n\n"
                    f"🌸 {to_small_caps('name')} : <b>{to_small_caps(char['name'])}</b>\n"
                    f"🏖️ {to_small_caps('anime')} : <b>{to_small_caps(char['anime'])}</b>\n"
                    f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
                    f"🆔️ {to_small_caps('id')} : <b>{char['id']}</b>\n\n"
                    f"{to_small_caps('globally guessed')} {g_count} {to_small_caps('times...')}"
                )
            
            results.append(
                InlineQueryResultPhoto(
                    id=f"{char['id']}_{time.time()}_{offset}",
                    photo_url=char['img_url'],
                    thumbnail_url=char['img_url'],
                    caption=caption,
                    parse_mode='HTML'
                )
            )
        
        # Logging for monitoring
        elapsed = time.time() - start_time
        logging.info(f"Inline query processed in {elapsed:.2f}s | Results: {len(results)}")
        
        await update.inline_query.answer(results, next_offset=next_offset, cache_time=0)
        
    except Exception as e:
        logging.error(f"Inline query error: {e}", exc_info=True)
        # Graceful degradation
        await update.inline_query.answer([], cache_time=0)

# --- Setup ---
application.add_handler(InlineQueryHandler(inlinequery, block=False))