import re
import time
from html import escape
from cachetools import TTLCache
from pymongo import ASCENDING

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import InlineQueryHandler, CallbackContext 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from shivu import user_collection, collection, application, db

RARITY_MAP = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ", 2: "🔵 ʀᴀʀᴇ", 3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ", 4: "💮 ꜱᴘᴇᴄɪᴀʟ",
    5: "👹 ᴀɴᴄɪᴇɴᴛ", 6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ", 7: "🔮 ᴇᴘɪᴄ", 8: "🪐 ᴄᴏꜱᴍɪᴄ",
    9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ", 10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ", 11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ",
    12: "🌸 ꜱᴘʀɪɴɢ", 13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ", 14: "🍭 ᴋᴀᴡᴀɪɪ", 15: "🧬 ʜʏʙʀɪᴅ"
}

def to_small_caps(text):
    if not text:
        return ""

    small_caps_map = {
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
        'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
        'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
        'Y': 'ʏ', 'Z': 'ᴢ',
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ',
        'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
        'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
        'y': 'ʏ', 'z': 'ᴢ'
    }
    return ''.join(small_caps_map.get(ch, ch) for ch in str(text))

db.characters.create_index([('id', ASCENDING)])
db.characters.create_index([('anime', ASCENDING)])
db.characters.create_index([('img_url', ASCENDING)])

db.user_collection.create_index([('characters.id', ASCENDING)])
db.user_collection.create_index([('characters.name', ASCENDING)])
db.user_collection.create_index([('characters.img_url', ASCENDING)])

all_characters_cache = TTLCache(maxsize=10000, ttl=120)
user_collection_cache = TTLCache(maxsize=10000, ttl=5)
global_count_cache = TTLCache(maxsize=50000, ttl=120)
anime_count_cache = TTLCache(maxsize=5000, ttl=120)

async def inlinequery(update: Update, context: CallbackContext) -> None:
    query = update.inline_query.query
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0

    if query.startswith('collection.') or (query.split()[0].isdigit() if query else False):
        if query.startswith('collection.'):
            user_id_str = query.split(' ')[0].split('.')[1]
            search_terms = ' '.join(query.split(' ')[1:])
        else:
            user_id_str = query.split(' ')[0]
            search_terms = ' '.join(query.split(' ')[1:])

        if user_id_str.isdigit():
            user_id = int(user_id_str)
            if user_id_str in user_collection_cache:
                user = user_collection_cache[user_id_str]
            else:
                user = await user_collection.find_one({'id': user_id})
                if user:
                    user_collection_cache[user_id_str] = user

            if user and 'characters' in user:
                all_characters = list({v['id']:v for v in user['characters']}.values())
                if search_terms:
                    try:
                        regex = re.compile(re.escape(search_terms), re.IGNORECASE)
                        all_characters = [character for character in all_characters if regex.search(character['name']) or regex.search(character['anime'])]
                    except:
                        all_characters = []
            else:
                all_characters = []
        else:
            all_characters = []
    else:
        if query:
            try:
                regex = re.compile(re.escape(query), re.IGNORECASE)
                all_characters = list(await collection.find({"$or": [{"name": regex}, {"anime": regex}]}).to_list(length=None))
            except:
                all_characters = []
        else:
            if 'all_characters' in all_characters_cache:
                all_characters = all_characters_cache['all_characters']
            else:
                all_characters = list(await collection.find({}).to_list(length=None))
                all_characters_cache['all_characters'] = all_characters

    characters = all_characters[offset:offset+50]
    next_offset = str(offset + 50) if len(all_characters) > offset + 50 else ""

    char_ids = [c['id'] for c in characters]
    anime_names = list(set([c['anime'] for c in characters]))

    valid_char_ids = set()
    if char_ids:
        valid_chars = await collection.find({'id': {'$in': char_ids}}, {'id': 1}).to_list(length=None)
        valid_char_ids = {c['id'] for c in valid_chars}

    global_counts = {}
    anime_counts = {}

    uncached_char_ids = []
    for cid in char_ids:
        if cid in valid_char_ids:
            cache_key = f"char_{cid}"
            if cache_key in global_count_cache:
                global_counts[cid] = global_count_cache[cache_key]
            else:
                uncached_char_ids.append(cid)

    if uncached_char_ids:
        pipeline = [
            {'$match': {'characters.id': {'$in': uncached_char_ids}}},
            {'$unwind': '$characters'},
            {'$match': {'characters.id': {'$in': uncached_char_ids}}},
            {'$group': {'_id': '$characters.id', 'count': {'$sum': 1}}}
        ]
        agg_results = await user_collection.aggregate(pipeline).to_list(length=None)
        for result in agg_results:
            cid = result['_id']
            count = result['count']
            global_counts[cid] = count
            global_count_cache[f"char_{cid}"] = count
        
        for cid in uncached_char_ids:
            if cid not in global_counts:
                global_counts[cid] = 0
                global_count_cache[f"char_{cid}"] = 0

    uncached_animes = []
    for anime in anime_names:
        cache_key = f"anime_{anime}"
        if cache_key in anime_count_cache:
            anime_counts[anime] = anime_count_cache[cache_key]
        else:
            uncached_animes.append(anime)

    if uncached_animes:
        pipeline = [
            {'$match': {'anime': {'$in': uncached_animes}}},
            {'$group': {'_id': '$anime', 'count': {'$sum': 1}}}
        ]
        agg_results = await collection.aggregate(pipeline).to_list(length=None)
        for result in agg_results:
            anime = result['_id']
            count = result['count']
            anime_counts[anime] = count
            anime_count_cache[f"anime_{anime}"] = count
        
        for anime in uncached_animes:
            if anime not in anime_counts:
                anime_counts[anime] = 0
                anime_count_cache[f"anime_{anime}"] = 0

    results = []
    for character in characters:
        if character['id'] not in valid_char_ids:
            continue

        global_count = global_counts.get(character['id'], 0)
        anime_characters = anime_counts.get(character['anime'], 0)

        rarity_value = character.get('rarity')
        rarity_display = to_small_caps("ɴ/ᴀ")

        if rarity_value is not None:
            try:
                if isinstance(rarity_value, int) or (isinstance(rarity_value, str) and rarity_value.isdigit()):
                    rarity_int = int(rarity_value)
                    if rarity_int in RARITY_MAP:
                        rarity_display = RARITY_MAP[rarity_int]
                    else:
                        rarity_display = to_small_caps(str(rarity_value))
                else:
                    rarity_display = to_small_caps(str(rarity_value))
            except (ValueError, TypeError):
                rarity_display = to_small_caps("ɴ/ᴀ")

        if query.startswith('collection.') or (query.split()[0].isdigit() if query else False):
            user_character_count = sum(1 for c in user['characters'] if c['id'] == character['id'])
            user_anime_characters = sum(1 for c in user['characters'] if c['anime'] == character['anime'])

            user_first_name = user.get('first_name', str(user['id']))

            caption = f"✨ {to_small_caps('look at')} {to_small_caps(escape(user_first_name))}'s {to_small_caps('character')}\n\n"
            caption += f"🌸{to_small_caps('name')} : <b>{to_small_caps(escape(character['name']))} (x{user_character_count})</b>\n"
            caption += f"🏖️{to_small_caps('anime')} : <b>{to_small_caps(escape(character['anime']))} ({user_anime_characters}/{anime_characters})</b>\n"
            caption += f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
            caption += f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>"
        else:
            caption = f"✨ {to_small_caps('look at this character !!')}\n\n"
            caption += f"🌸{to_small_caps('name')} : <b>{to_small_caps(escape(character['name']))}</b>\n"
            caption += f"🏖️{to_small_caps('anime')} : <b>{to_small_caps(escape(character['anime']))}</b>\n"
            caption += f"🏵️ {to_small_caps('rarity')} : <b>{rarity_display}</b>\n"
            caption += f"🆔️ {to_small_caps('id')} : <b>{character['id']}</b>\n\n"
            caption += f"{to_small_caps('globally guessed')} {global_count} {to_small_caps('times...')}"

        results.append(
            InlineQueryResultPhoto(
                thumbnail_url=character['img_url'],
                id=f"{character['id']}_{time.time()}",
                photo_url=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
        )

    await update.inline_query.answer(results, next_offset=next_offset, cache_time=0)

application.add_handler(InlineQueryHandler(inlinequery, block=False))
