from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from html import escape
import math
import asyncio
import functools
from typing import Dict, List, Optional, Any
import hashlib
import re
import pickle
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

try:
    import orjson as json
except ImportError:
    import json

try:
    from motor.motor_asyncio import AsyncIOMotorClient
    MOTOR_AVAILABLE = True
except ImportError:
    MOTOR_AVAILABLE = False

try:
    import redis.asyncio as redis
    from redis.asyncio.connection import ConnectionPool
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from shivu import collection, user_collection, application

CACHE_TTL = 300
PAGE_SIZE = 15
MAX_CONNECTIONS = 50

_thread_pool = ThreadPoolExecutor(max_workers=4)

redis_pool = None
redis_client = None
redis_working = False

if REDIS_AVAILABLE:
    try:
        redis_pool = ConnectionPool(
            host='localhost', 
            port=6379, 
            db=0, 
            decode_responses=False,
            max_connections=20,
            socket_keepalive=True,
            socket_connect_timeout=2,
            socket_timeout=2
        )
        redis_client = redis.Redis(connection_pool=redis_pool, socket_connect_timeout=2, socket_timeout=2)
        redis_working = True
    except Exception:
        redis_client = None
        redis_working = False

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

@functools.lru_cache(maxsize=5000)
def to_small)
def to_small_caps(text: str) -> str:
    if not text:
        return ""
    return str(text).translate(_SMALL_CAPS_MAP)

_RARITY_EXTRACTOR = re.compile(r'\[([^\]]+)\]')

RARITY_DATA = {
    1: ("⚪", "ᴄᴏᴍᴍᴏɴ"),
    2: ("🔵", "ʀᴀʀᴇ"),
    3: ("🟡", "ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: ("💮", "ꜱᴘᴇᴄɪᴀʟ"),
    5: ("👹", "ᴀɴᴄɪᴇɴᴛ"),
    6: ("🎐", "ᴄᴇʟᴇꜱᴛɪᴀʟ"),
    7: ("🔮", "ᴇᴘɪᴄ"),
    8: ("🪐", "ᴄᴏꜱᴍɪᴄ"),
    9: ("⚰️", "ɴɪɢʜᴛᴍᴀʀᴇ"),
    10: ("🌬️", "ꜰʀᴏꜱᴛʙᴏʀɴ"),
    11: ("💝", "ᴠᴀʟᴇɴᴛɪɴᴇ"),
    12: ("🌸", "ꜱᴘʀɪɴɢ"),
    13: ("🏖️", "ᴛʀᴏᴘɪᴄᴀʟ"),
    14: ("🍭", "ᴋᴀᴡᴀɪɪ"),
    15: ("🧬", "ʜʏʙʀɪᴅ")
}

RARITY_EMOJIS = {k: v[0] for k, v in RARITY_DATA.items()}

EMOJI_TO_RARITY = {}
RARITY_STRINGS = {}
for num, (emoji, name) in RARITY_DATA.items():
    EMOJI_TO_RARITY[emoji] = num
    EMOJI_TO_RARITY[f"{emoji} {name}"] = num
    RARITY_STRINGS[num] = f"{emoji} {name}"

@functools.lru_cache(maxsize=1000)
def parse_rarity(rarity_value: tuple) -> int:
    if not rarity_value:
        return 1
    if isinstance(rarity_value[0], int):
        return rarity_value[0] if rarity_value[0] in RARITY_DATA else 1
    if isinstance(rarity_value[0], str):
        r_str = rarity_value[0].strip()
        if r_str.isdigit():
            r_int = int(r_str)
            return r_int if r_int in RARITY_DATA else 1
        return EMOJI_TO_RARITY.get(r_str, 1)
    return 1

def extract_rarity_from_name(name: str) -> int:
    if not name:
        return 1
    matches = _RARITY_EXTRACTOR.findall(name)
    for match in matches:
        for emoji, num in EMOJI_TO_RARITY.items():
            if emoji in match:
                return num
    return 1

class CacheManager:
    def __init__(self):
        self._local_cache = {}
        self._lock = asyncio.Lock()
        self._pending = {}
    
    async def get(self, key: str):
        global redis_working
        if not redis_working or not redis_client:
            return self._local_cache.get(key)
        
        try:
            data = await asyncio.wait_for(redis_client.get(key), timeout=1.0)
            if data:
                return pickle.loads(data)
        except Exception:
            redis_working = False
        return None
    
    async def set(self, key: str, value: Any, ttl: int = CACHE_TTL):
        global redis_working
        if not redis_working or not redis_client:
            self._local_cache[key] = (value, time.time() + ttl)
            return
        
        try:
            serialized = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            await asyncio.wait_for(redis_client.setex(key, ttl, serialized), timeout=1.0)
        except Exception:
            redis_working = False
    
    async def get_or_set(self, key: str, factory, ttl: int = CACHE_TTL):
        cached = await self.get(key)
        if cached is not None:
            return cached
        
        if key in self._pending:
            return await self._pending[key]
        
        future = asyncio.Future()
        self._pending[key] = future
        
        try:
            value = await factory()
            await self.set(key, value, ttl)
            future.set_result(value)
            return value
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            if key in self._pending:
                del self._pending[key]

cache_manager = CacheManager()

class HaremManagerV4:
    
    @staticmethod
    async def get_user_data_optimized(user_id: int, rarity_filter: Optional[int] = None):
        cache_key = f"user_harem:{user_id}:{rarity_filter}"
        
        async def fetch_data():
            pipeline = [
                {"$match": {"id": user_id}},
                {"$project": {
                    "characters": 1,
                    "favorites": 1,
                    "name": 1,
                    "_id": 0
                }}
            ]
            
            cursor = user_collection.aggregate(
                pipeline, 
                allowDiskUse=False,
                batchSize=1
            )
            
            user_data = await cursor.to_list(1)
            if not user_data:
                return None, []
            
            user = user_data[0]
            characters = user.get('characters', [])
            
            if not characters:
                return user, []
            
            if rarity_filter:
                filtered = []
                for char in characters:
                    r_val = char.get('rarity')
                    if isinstance(r_val, (int, str)):
                        if parse_rarity((r_val,)) == rarity_filter:
                            filtered.append(char)
                characters = filtered
            
            return user, characters
        
        return await cache_manager.get_or_set(cache_key, fetch_data, ttl=60)
    
    @staticmethod
    async def get_character_details_optimized(char_ids: List[str]):
        if not char_ids:
            return {}
        
        unique_ids = list(set(char_ids))
        cache_hits = {}
        missing_ids = []
        
        cache_keys = [f"char:{cid}" for cid in unique_ids]
        
        if redis_working and redis_client:
            try:
                pipe = redis_client.pipeline()
                for key in cache_keys:
                    pipe.get(key)
                results = await asyncio.wait_for(pipe.execute(), timeout=1.0)
                
                for cid, data in zip(unique_ids, results):
                    if data:
                        cache_hits[cid] = pickle.loads(data)
                    else:
                        missing_ids.append(cid)
            except Exception:
                missing_ids = unique_ids
        else:
            missing_ids = unique_ids
        
        if not missing_ids:
            return cache_hits
        
        projection = {
            "id": 1, "name": 1, "anime": 1, 
            "rarity": 1, "img_url": 1, "_id": 0
        }
        
        cursor = collection.find(
            {"id": {"$in": missing_ids}},
            projection
        ).batch_size(len(missing_ids))
        
        char_map = {}
        
        async for char in cursor:
            cid = char['id']
            char_map[cid] = char
            
            if redis_working and redis_client:
                try:
                    serialized = pickle.dumps(char, protocol=pickle.HIGHEST_PROTOCOL)
                    await asyncio.wait_for(redis_client.setex(f"char:{cid}", CACHE_TTL, serialized), timeout=0.5)
                except Exception:
                    pass
        
        char_map.update(cache_hits)
        return char_map
    
    @staticmethod
    async def get_anime_counts_optimized(animes: List[str]):
        if not animes:
            return {}
        
        cache_key = f"anime_counts:{hash(tuple(sorted(animes)))}"
        cached = await cache_manager.get(cache_key)
        if cached:
            return cached
        
        pipeline = [
            {"$match": {"anime": {"$in": animes}}},
            {"$group": {"_id": "$anime", "count": {"$sum": 1}}}
        ]
        
        results = {}
        async for doc in collection.aggregate(pipeline):
            results[doc['_id']] = doc['count']
        
        await cache_manager.set(cache_key, results, ttl=300)
        return results

def build_harem_message_sync(
    user_name: str,
    page: int,
    total_pages: int,
    total_count: int,
    rarity_filter: Optional[int],
    display_chars: List[dict],
    anime_counts: Dict[str, int]
) -> str:
    
    safe_name = escape(user_name)
    header_parts = [f"<b>{to_small_caps(f'{safe_name} S HAREM - PAGE {page+1}/{total_pages}')}</b>"]
    
    if rarity_filter:
        filter_emoji = RARITY_EMOJIS.get(rarity_filter, '⚪')
        header_parts.append(f"<b>{to_small_caps(f'FILTER: {filter_emoji} ({total_count})')}</b>")
    
    harem_msg = "\n".join(header_parts) + "\n\n"
    
    anime_groups = defaultdict(list)
    for char in display_chars:
        anime = char.get('anime', 'Unknown')
        anime_groups[anime].append(char)
    
    for anime, chars in anime_groups.items():
        safe_anime = escape(str(anime))
        total_in_anime = anime_counts.get(anime, len(chars))
        
        harem_msg += f"<b>𖤍 {to_small_caps(safe_anime)} {{{len(chars)}/{total_in_anime}}}</b>\n"
        harem_msg += f"{to_small_caps('--------------------')}\n"
        
        for char in chars:
            name = to_small_caps(escape(char.get('name', 'Unknown')))
            rarity = char.get('rarity', 1)
            emoji = RARITY_EMOJIS.get(rarity, '⚪')
            count = char.get('count', 1)
            
            harem_msg += f"✶ {char['id']} [{emoji}] {name} x{count}\n"
        
        harem_msg += f"{to_small_caps('--------------------')}\n\n"
    
    return harem_msg

async def harem_v4(update: Update, context: CallbackContext, page: int = 0):
    start_time = time.time()
    user_id = update.effective_user.id
    
    rarity_filter = None
    try:
        import asyncio
        from shivu.modules.smode import get_user_sort_preference
        rarity_filter = await asyncio.wait_for(
            get_user_sort_preference(user_id), 
            timeout=0.5
        )
        if rarity_filter:
            rarity_filter = int(rarity_filter)
    except Exception:
        pass
    
    result = await HaremManagerV4.get_user_data_optimized(user_id, rarity_filter)
    if not result:
        await _send_response(update, to_small_caps("You Have Not Guessed any Characters Yet.."))
        return
    
    user, user_chars = result
    
    if not user_chars:
        msg = to_small_caps(f"No Characters Of This Rarity! Use /smode") if rarity_filter else to_small_caps("You Have Not Guessed any Characters Yet..")
        await _send_response(update, msg)
        return
    
    total_count = len(user_chars)
    
    char_counts = {}
    unique_ids = []
    seen = set()
    user_rarity_map = {}
    
    for char in user_chars:
        cid = char.get('id')
        if cid:
            char_counts[cid] = char_counts.get(cid, 0) + 1
            if cid not in seen:
                seen.add(cid)
                unique_ids.append(cid)
                r = char.get('rarity')
                user_rarity_map[cid] = parse_rarity((r,)) if r else 1
    
    total_unique = len(unique_ids)
    total_pages = max(1, math.ceil(total_unique / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * PAGE_SIZE
    page_ids = unique_ids[start_idx:start_idx + PAGE_SIZE]
    
    char_task = asyncio.create_task(
        HaremManagerV4.get_character_details_optimized(page_ids)
    )
    
    display_chars = []
    
    char_details = await char_task
    
    page_animes = set()
    
    for cid in page_ids:
        if cid in char_details:
            char_data = char_details[cid]
            page_animes.add(char_data.get('anime'))
            
            name = char_data.get('name', '')
            name_rarity = extract_rarity_from_name(name)
            
            display_chars.append({
                'id': cid,
                'name': name,
                'anime': char_data.get('anime', 'Unknown'),
                'rarity': name_rarity if name_rarity != 1 else user_rarity_map.get(cid, 1),
                'count': char_counts[cid]
            })
    
    anime_counts_task = asyncio.create_task(
        HaremManagerV4.get_anime_counts_optimized(list(page_animes))
    )
    
    display_chars.sort(key=lambda x: x['anime'])
    
    anime_counts = await anime_counts_task
    
    loop = asyncio.get_event_loop()
    harem_msg = await loop.run_in_executor(
        _thread_pool,
        build_harem_message_sync,
        update.effective_user.first_name,
        page,
        total_pages,
        total_count,
        rarity_filter,
        display_chars,
        anime_counts
    )
    
    keyboard = [[
        InlineKeyboardButton(
            to_small_caps(f"🔮 See Collection ({total_count})"),
            switch_inline_query_current_chat=f"collection.{user_id}"
        )
    ], [
        InlineKeyboardButton(
            "❌ " + to_small_caps("Cancel"),
            callback_data=f"open_smode:{user_id}"
        )
    ]]
    
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{page-1}:{user_id}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"harem:{page+1}:{user_id}"))
        keyboard.append(nav_buttons)
    
    markup = InlineKeyboardMarkup(keyboard)
    
    photo_url = None
    if user.get('favorites'):
        fav_id = user['favorites'][0]
        if fav_id in char_details:
            photo_url = char_details[fav_id].get('img_url')
    
    if not photo_url and display_chars:
        photo_url = display_chars[0].get('img_url')
    
    try:
        if photo_url:
            if update.message:
                await update.message.reply_photo(
                    photo_url, 
                    caption=harem_msg, 
                    reply_markup=markup, 
                    parse_mode='HTML',
                    write_timeout=5,
                    connect_timeout=5
                )
            else:
                await update.callback_query.edit_message_caption(
                    caption=harem_msg, 
                    reply_markup=markup, 
                    parse_mode='HTML'
                )
        else:
            await _send_response(update, harem_msg, markup)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            await _send_response(update, harem_msg, markup)
    
    elapsed = time.time() - start_time
    if elapsed > 1.0:
        print(f"[HAREM-V4] Slow response: {elapsed:.2f}s for user {user_id}")

async def _send_response(update: Update, text: str, markup=None):
    try:
        if update.message:
            await update.message.reply_text(
                text, 
                reply_markup=markup, 
                parse_mode='HTML',
                write_timeout=5
            )
        else:
            await update.callback_query.edit_message_text(
                text, 
                reply_markup=markup, 
                parse_mode='HTML'
            )
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        elif update.callback_query:
            try:
                await update.callback_query.answer(to_small_caps("Updated"), show_alert=False)
            except:
                pass

async def harem_callback_v4(update: Update, context: CallbackContext):
    query = update.callback_query
    
    try:
        _, page, user_id = query.data.split(':')
        page, user_id = int(page), int(user_id)
    except:
        await query.answer(to_small_caps("Invalid"), show_alert=True)
        return
    
    if query.from_user.id != user_id:
        await query.answer(to_small_caps("Not Your Harem"), show_alert=True)
        return
    
    await query.answer()
    await harem_v4(update, context, page)

application.add_handler(CommandHandler(["harem", "collection"], harem_v4, block=False))
application.add_handler(CallbackQueryHandler(harem_callback_v4, pattern=r'^harem:', block=False))
