from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from html import escape
import math
import asyncio
import functools
import hashlib
import re



from shivu.utils import to_small_caps

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
for num, (emoji, name) in RARITY_DATA.items():
    EMOJI_TO_RARITY[emoji] = num
    EMOJI_TO_RARITY[f"{emoji} {name}"] = num

def parse_rarity(rarity_value) -> int:
    if rarity_value is None:
        return 1
    if isinstance(rarity_value, int):
        return rarity_value if rarity_value in RARITY_DATA else 1
    if isinstance(rarity_value, str):
        rarity_str = rarity_value.strip()
        if rarity_str.isdigit():
            return int(rarity_str) if int(rarity_str) in RARITY_DATA else 1
        if rarity_str in EMOJI_TO_RARITY:
            return EMOJI_TO_RARITY[rarity_str]
        for emoji, num in EMOJI_TO_RARITY.items():
            if emoji in rarity_str:
                return num
    return 1

def extract_rarity_from_name(name: str) -> int:
    if not name:
        return 1
    matches = re.findall(r'\[([^\]]+)\]', name)
    for match in matches:
        for emoji, num in EMOJI_TO_RARITY.items():
            if emoji in match:
                return num
    return 1



async def get_user_characters_fast(user_id: int, rarity_filter: int | None = None):
    cache_key = (user_id, rarity_filter)
    if cache_key in _harem_cache:
        return _harem_cache[cache_key]

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
            filtered_chars = []
            for char in characters:
                char_rarity = parse_rarity(char.get('rarity'))
                if char_rarity == rarity_filter:
                    filtered_chars.append(char)
            characters = filtered_chars
        
        return user, characters
    
async def get_character_details_batch(char_ids: list[str]):
    if not char_ids:
        return {}
    
    unique_ids = list(set(char_ids))
    
    projection = {
        "id": 1, 
        "name": 1, 
        "anime": 1, 
        "rarity": 1,
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

async def get_anime_counts_batch(animes: list[str]):
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

async def harem_v3(update: Update, context: CallbackContext, page: int = 0):
    user_id = update.effective_user.id
    
    rarity_filter = None
    try:
        from shivu.modules.smode import get_user_sort_preference
        rarity_filter = await get_user_sort_preference(user_id)
        if rarity_filter:
            rarity_filter = int(rarity_filter)
    except:
        pass
    
    user, user_chars = await get_user_characters_fast(user_id, rarity_filter)
    
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
    
    char_id_counts = {}
    unique_char_ids = []
    seen = set()
    user_rarity_map = {}
    
    for char in user_chars:
        cid = char.get('id')
        if cid:
            char_id_counts[cid] = char_id_counts.get(cid, 0) + 1
            if cid not in seen:
                seen.add(cid)
                unique_char_ids.append(cid)
            user_rarity_map[cid] = parse_rarity(char.get('rarity'))
    
    total_unique = len(unique_char_ids)
    total_pages = max(1, math.ceil(total_unique / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_ids = unique_char_ids[start_idx:end_idx]
    
    char_details = await get_character_details_batch(page_ids)
    
    display_chars = []
    for cid in page_ids:
        if cid in char_details:
            char_data = char_details[cid].copy()
            char_data['count'] = char_id_counts[cid]
            
            user_rarity = user_rarity_map.get(cid, 1)
            name_rarity = extract_rarity_from_name(char_data.get('name', ''))
            
            if name_rarity != 1:
                char_data['rarity'] = name_rarity
            else:
                char_data['rarity'] = user_rarity
            
            display_chars.append(char_data)
    
    display_chars.sort(key=lambda x: x.get('anime', ''))
    
    page_animes = list({c.get('anime') for c in display_chars})
    anime_counts_task = asyncio.create_task(
        get_anime_counts_batch(page_animes)
    )
    
    safe_name = escape(update.effective_user.first_name)
    header = f"<b>{to_small_caps(f'{safe_name} S HAREM - PAGE {page+1}/{total_pages}')}</b>\n"
    
    if rarity_filter:
        filter_emoji = RARITY_EMOJIS.get(rarity_filter, '⚪')
        header += f"<b>{to_small_caps(f'FILTER: {filter_emoji} ({total_count})')}</b>\n"
    
    harem_msg = header + "\n"
    
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
            
            rarity = char.get('rarity', 1)
            if isinstance(rarity, str):
                rarity = parse_rarity(rarity)
            
            emoji = RARITY_EMOJIS.get(rarity, '⚪')
            count = char.get('count', 1)
            
            harem_msg += f"✶ {char['id']} [{emoji}] {name} x{count}\n"
        
        harem_msg += f"{to_small_caps('--------------------')}\n\n"
    
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

async def harem_callback_v3(update: Update, context: CallbackContext):
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

application.add_handler(CommandHandler(["harem", "collection"], harem_v3, block=False))
application.add_handler(CallbackQueryHandler(harem_callback_v3, pattern=r'^harem:', block=False))
