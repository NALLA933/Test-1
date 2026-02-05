import random
import time
from datetime import datetime, timezone, timedelta
from html import escape
from typing import List, Dict, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler

from shivu import collection, user_collection, application


def to_small_caps(text: str) -> str:
    small_caps_mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ',
        'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ',
        'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ',
        'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ',
        'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ',
        'z': 'ᴢ',
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ',
        'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ',
        'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ',
        'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ',
        'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ',
        'Z': 'ᴢ',
        ' ': ' ', '-': '-', '/': '/', '(': '(', ')': ')',
        '[': '[', ']': ']', '{': '{', '}': '}', ':': ':',
        '.': '.', ',': ',', '!': '!', '?': '?', '\'': '\'',
        '"': '"', '&': '&', '@': '@', '#': '#', '$': '$',
        '%': '%', '^': '^', '*': '*', '+': '+', '=': '=',
        '_': '_', '|': '|', '\\': '\\', '`': '`', '~': '~',
        '<': '<', '>': '>', ';': ';', '\n': '\n',
        '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
        '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
    }
    return ''.join(small_caps_mapping.get(char, char) for char in str(text))


RARITY_EMOJIS = {
    1: '⚪', 2: '🔵', 3: '🟡', 4: '💮', 5: '👹',
    6: '🎐', 7: '🔮', 8: '🪐', 9: '⚰️', 10: '🌬️',
    11: '💝', 12: '🌸', 13: '🏖️', 14: '🍭', 15: '🧬'
}

RARITY_NAMES = {
    1: "ᴄᴏᴍᴍᴏɴ", 2: "ʀᴀʀᴇ", 3: "ʟᴇɢᴇɴᴅᴀʀʏ", 4: "ꜱᴘᴇᴄɪᴀʟ", 5: "ᴀɴᴄɪᴇɴᴛ",
    6: "ᴄᴇʟᴇꜱᴛɪᴀʟ", 7: "ᴇᴘɪᴄ", 8: "ᴄᴏꜱᴍɪᴄ", 9: "ɴɪɢʜᴛᴍᴀʀᴇ", 10: "ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "ᴠᴀʟᴇɴᴛɪɴᴇ", 12: "ꜱᴘʀɪɴɢ", 13: "ᴛʀᴏᴘɪᴄᴀʟ", 14: "ᴋᴀᴡᴀɪɪ", 15: "ʜʏʙʀɪᴅ"
}

SHOP_RARITIES = [4, 5, 6, 14]

PRICE_RANGES = {
    4: (400000, 500000),
    5: (600000, 700000),
    6: (650000, 750000),
    14: (450000, 550000),
}

DISCOUNT_MIN = 5
DISCOUNT_MAX = 15

REFRESH_COST = 20000

IST_OFFSET = timedelta(hours=5, minutes=30)


def get_rarity_from_string(rarity_val) -> int:
    if rarity_val is None:
        return 0
    
    if isinstance(rarity_val, int):
        return rarity_val
    
    if isinstance(rarity_val, str):
        rarity_val = rarity_val.strip()
        
        if rarity_val.isdigit():
            return int(rarity_val)
        
        rarity_to_int = {
            '⚪ ᴄᴏᴍᴍᴏɴ': 1, '🔵 ʀᴀʀᴇ': 2, '🟡 ʟᴇɢᴇɴᴅᴀʀʏ': 3, '💮 ꜱᴘᴇᴄɪᴀʟ': 4,
            '👹 ᴀɴᴄɪᴇɴᴛ': 5, '🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ': 6, '🔮 ᴇᴘɪᴄ': 7, '🪐 ᴄᴏꜱᴍɪᴄ': 8,
            '⚰️ ɴɪɢʜᴛᴍᴀʀᴇ': 9, '🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ': 10, '💝 ᴠᴀʟᴇɴᴛɪɴᴇ': 11,
            '🌸 ꜱᴘʀɪɴɢ': 12, '🏖️ ᴛʀᴏᴘɪᴄᴀʟ': 13, '🍭 ᴋᴀᴡᴀɪɪ': 14, '🧬 ʜʏʙʀɪᴅ': 15,
            'common': 1, 'rare': 2, 'legendary': 3, 'special': 4,
            'ancient': 5, 'celestial': 6, 'epic': 7, 'cosmic': 8,
            'nightmare': 9, 'frostborn': 10, 'valentine': 11,
            'spring': 12, 'tropical': 13, 'kawaii': 14, 'hybrid': 15
        }
        
        result = rarity_to_int.get(rarity_val.lower(), 0)
        if result == 0:
            for key, value in rarity_to_int.items():
                if rarity_val.lower() in key.lower() or key.lower() in rarity_val.lower():
                    return value
        
        return result
    
    return 0


def get_ist_midnight() -> datetime:
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFFSET
    next_midnight_ist = (now_ist + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_midnight_utc = next_midnight_ist - IST_OFFSET
    return next_midnight_utc


async def get_balance(user_id: int) -> int:
    user = await user_collection.find_one({'id': user_id})
    if not user:
        return 0
    return int(user.get('balance', 0))


async def change_balance(user_id: int, amount: int) -> int:
    await user_collection.update_one(
        {"id": user_id},
        {"$inc": {"balance": int(amount)}},
        upsert=True
    )
    user = await user_collection.find_one({'id': user_id})
    return int(user.get('balance', 0)) if user else 0


async def get_user_owned_characters(user_id: int) -> List[str]:
    user = await user_collection.find_one({'id': user_id})
    if not user:
        return []
    characters = user.get('characters', [])
    owned_ids = [char.get('id') for char in characters if char.get('id')]
    return list(set(owned_ids))


async def get_character_owner_count(char_id: str) -> int:
    count = await user_collection.count_documents({
        'characters.id': char_id
    })
    return count


async def add_character_to_user(user_id: int, character: dict) -> bool:
    try:
        char_data = {
            'id': character['id'],
            'name': character['name'],
            'anime': character['anime'],
            'rarity': character.get('rarity', 1),
            'img_url': character.get('img_url', '')
        }
        await user_collection.update_one(
            {'id': user_id},
            {
                '$push': {'characters': char_data},
                '$setOnInsert': {'id': user_id, 'balance': 0}
            },
            upsert=True
        )
        return True
    except Exception as e:
        print(f"Error adding character to user: {e}")
        return False


async def get_shop_data(user_id: int, force_reset: bool = False) -> dict:
    user = await user_collection.find_one({'id': user_id})
    
    if not user or force_reset:
        shop_data = await initialize_shop_data(user_id)
        return shop_data
    
    shop_data = user.get('shop_data', {})
    
    if not shop_data or 'characters' not in shop_data:
        shop_data = await initialize_shop_data(user_id)
        return shop_data
    
    last_reset = shop_data.get('last_reset', 0)
    current_time = time.time()
    
    if last_reset < (current_time - 86400):
        shop_data = await initialize_shop_data(user_id)
        return shop_data
    
    if not shop_data.get('characters'):
        shop_data = await initialize_shop_data(user_id)
        return shop_data
    
    return shop_data


async def initialize_shop_data(user_id: int) -> dict:
    all_chars = []
    async for char in collection.find({}):
        all_chars.append(char)
    
    if not all_chars:
        return {'characters': [], 'last_reset': time.time(), 'refresh_used': False, 'current_index': 0}
    
    shop_rarity_chars = []
    for char in all_chars:
        rarity = get_rarity_from_string(char.get('rarity'))
        if rarity in SHOP_RARITIES:
            shop_rarity_chars.append(char)
    
    if len(shop_rarity_chars) == 0:
        return {'characters': [], 'last_reset': time.time(), 'refresh_used': False, 'current_index': 0}
    
    if len(shop_rarity_chars) <= 3:
        selected_chars = shop_rarity_chars
    else:
        selected_chars = random.sample(shop_rarity_chars, 3)
    
    characters = []
    for char in selected_chars:
        rarity = get_rarity_from_string(char.get('rarity', 4))
        price_range = PRICE_RANGES.get(rarity, (400000, 500000))
        base_price = random.randint(price_range[0], price_range[1])
        discount_percent = random.randint(DISCOUNT_MIN, DISCOUNT_MAX)
        discount_amount = int(base_price * discount_percent / 100)
        final_price = base_price - discount_amount
        
        characters.append({
            'id': char['id'],
            'name': char['name'],
            'anime': char['anime'],
            'rarity': rarity,
            'img_url': char.get('img_url', ''),
            'base_price': base_price,
            'discount_percent': discount_percent,
            'final_price': final_price
        })
    
    shop_data = {
        'characters': characters,
        'last_reset': time.time(),
        'refresh_used': False,
        'current_index': 0
    }
    
    await user_collection.update_one(
        {'id': user_id},
        {
            '$set': {'shop_data': shop_data},
            '$setOnInsert': {'id': user_id, 'balance': 0, 'characters': []}
        },
        upsert=True
    )
    
    return shop_data


async def refresh_shop(user_id: int) -> Tuple[bool, str]:
    user = await user_collection.find_one({'id': user_id})
    if not user:
        return False, to_small_caps("Error: User not found")
    
    shop_data = user.get('shop_data', {})
    if shop_data.get('refresh_used', False):
        return False, to_small_caps("⚠️ You have reached daily limit of 1 refresh!")
    
    balance = await get_balance(user_id)
    if balance < REFRESH_COST:
        return False, to_small_caps(f"⚠️ Insufficient balance! Need {REFRESH_COST:,} coins")
    
    await change_balance(user_id, -REFRESH_COST)
    
    all_chars = []
    async for char in collection.find({}):
        all_chars.append(char)
    
    shop_rarity_chars = []
    for char in all_chars:
        rarity = get_rarity_from_string(char.get('rarity'))
        if rarity in SHOP_RARITIES:
            shop_rarity_chars.append(char)
    
    if len(shop_rarity_chars) == 0:
        return False, to_small_caps("No characters available for shop!")
    
    if len(shop_rarity_chars) <= 3:
        selected_chars = shop_rarity_chars
    else:
        selected_chars = random.sample(shop_rarity_chars, 3)
    
    characters = []
    for char in selected_chars:
        rarity = get_rarity_from_string(char.get('rarity', 4))
        price_range = PRICE_RANGES.get(rarity, (400000, 500000))
        base_price = random.randint(price_range[0], price_range[1])
        discount_percent = random.randint(DISCOUNT_MIN, DISCOUNT_MAX)
        discount_amount = int(base_price * discount_percent / 100)
        final_price = base_price - discount_amount
        
        characters.append({
            'id': char['id'],
            'name': char['name'],
            'anime': char['anime'],
            'rarity': rarity,
            'img_url': char.get('img_url', ''),
            'base_price': base_price,
            'discount_percent': discount_percent,
            'final_price': final_price
        })
    
    shop_data['characters'] = characters
    shop_data['refresh_used'] = True
    shop_data['current_index'] = 0
    
    await user_collection.update_one(
        {'id': user_id},
        {'$set': {'shop_data': shop_data}}
    )
    
    return True, to_small_caps(f"✅ Shop refreshed! Cost: {REFRESH_COST:,} coins")


async def shop_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    shop_data = await get_shop_data(user_id)
    
    if not shop_data.get('characters'):
        await update.message.reply_text(
            to_small_caps("⚠️ Shop is empty! Please try again later.")
        )
        return
    
    await display_shop_character(update, context, user_id, 0)


async def display_shop_character(update: Update, context: CallbackContext, 
                                 user_id: int, index: int) -> None:
    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])
    
    if not characters:
        message = to_small_caps("⚠️ Shop is empty! Please try again later.")
        if update.message:
            await update.message.reply_text(message)
        else:
            await update.callback_query.edit_message_text(message)
        return
    
    index = max(0, min(index, len(characters) - 1))
    
    await user_collection.update_one(
        {'id': user_id},
        {'$set': {'shop_data.current_index': index}}
    )
    
    char = characters[index]
    owner_count = await get_character_owner_count(char['id'])
    owned_chars = await get_user_owned_characters(user_id)
    status = "Sold" if char['id'] in owned_chars else "Available"
    
    rarity_emoji = RARITY_EMOJIS.get(char['rarity'], '⚪')
    rarity_name = RARITY_NAMES.get(char['rarity'], 'ᴜɴᴋɴᴏᴡɴ')
    
    safe_name = escape(str(char['name']))
    safe_anime = escape(str(char['anime']))
    
    message = f"<b>🏪 {to_small_caps(f'Character Shop ({index + 1}/{len(characters)})')}</b>\n\n"
    message += f"🎭 {to_small_caps('Name')}: {to_small_caps(safe_name)}\n"
    message += f"📺 {to_small_caps('Anime')}: {to_small_caps(safe_anime)}\n"
    message += f"🆔 {to_small_caps('Id')}: {char['id']}\n"
    message += f"✨ {to_small_caps('Rarity')}: {rarity_emoji} {rarity_name}\n"
    message += f"💸 {to_small_caps('Price')}: {char['base_price']:,}\n"
    message += f"🛒 {to_small_caps('Discount')}: {char['discount_percent']}%\n"
    message += f"🏷️ {to_small_caps('Discount Price')}: {char['final_price']:,}\n"
    message += f"🎴 {to_small_caps('Owner')}: {owner_count}\n"
    message += f"📋 {to_small_caps('Stats')}: {to_small_caps(status)}\n"
    
    keyboard = []
    
    if status == "Available":
        keyboard.append([
            InlineKeyboardButton(
                to_small_caps("💰 Purchase"),
                callback_data=f"shop_purchase:{user_id}:{index}"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                to_small_caps("❌ Already Owned"),
                callback_data="shop_noop"
            )
        ])
    
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"shop_nav:{user_id}:{index - 1}"))
    
    nav_row.append(InlineKeyboardButton(
        f"🍃 {to_small_caps('Refresh')}",
        callback_data=f"shop_refresh:{user_id}"
    ))
    
    if index < len(characters) - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"shop_nav:{user_id}:{index + 1}"))
    
    keyboard.append(nav_row)
    
    keyboard.append([
        InlineKeyboardButton(
            f"💸 {to_small_caps('Premium Shop')}",
            callback_data=f"shop_premium:{user_id}"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            to_small_caps("❌ Close"),
            callback_data=f"shop_close:{user_id}"
        )
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    photo_url = char.get('img_url')
    
    if update.message:
        if photo_url:
            await update.message.reply_photo(
                photo=photo_url,
                caption=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    else:
        query = update.callback_query
        
        if photo_url:
            try:
                current_photo = query.message.photo
                if current_photo:
                    current_file_id = current_photo[-1].file_id
                    
                    async with application.bot.get_file(current_file_id) as file:
                        pass
                    
                    new_media = InputMediaPhoto(media=photo_url, caption=message, parse_mode='HTML')
                    await query.edit_message_media(media=new_media, reply_markup=reply_markup)
                else:
                    await query.delete_message()
                    await query.message.reply_photo(
                        photo=photo_url,
                        caption=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
            except Exception as e:
                try:
                    await query.delete_message()
                    await query.message.reply_photo(
                        photo=photo_url,
                        caption=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
                except:
                    await query.edit_message_text(
                        message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
        else:
            try:
                if query.message.photo:
                    await query.delete_message()
                    await query.message.reply_text(
                        message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
                else:
                    await query.edit_message_text(
                        message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
            except:
                await query.edit_message_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )


async def shop_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    data = query.data
    
    if data == "shop_noop":
        await query.answer()
        return
    
    parts = data.split(':')
    action = parts[0]
    
    if action == "shop_nav":
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        await display_shop_character(update, context, user_id, index)
        await query.answer()
    
    elif action == "shop_refresh":
        _, user_id = parts
        user_id = int(user_id)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        success, message = await refresh_shop(user_id)
        
        if success:
            await query.answer(message, show_alert=True)
            await display_shop_character(update, context, user_id, 0)
        else:
            await query.answer(message, show_alert=True)
    
    elif action == "shop_premium":
        _, user_id = parts
        user_id = int(user_id)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        await query.answer(
            f"💸 {to_small_caps('Premium Shop')}\n\n✨ {to_small_caps('Coming Soon...')}",
            show_alert=True
        )
    
    elif action == "shop_purchase":
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        await show_purchase_confirmation(update, context, user_id, index)
        await query.answer()
    
    elif action == "shop_confirm_purchase":
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        await process_purchase(update, context, user_id, index)
    
    elif action == "shop_cancel_purchase":
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        await query.answer(to_small_caps("Purchase cancelled"))
        await display_shop_character(update, context, user_id, index)
    
    elif action == "shop_close":
        _, user_id = parts
        user_id = int(user_id)
        
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return
        
        try:
            await query.message.delete()
        except:
            await query.edit_message_text(to_small_caps("Shop closed."))
        await query.answer()


async def show_purchase_confirmation(update: Update, context: CallbackContext,
                                     user_id: int, index: int) -> None:
    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])
    
    if index >= len(characters):
        return
    
    char = characters[index]
    owned_chars = await get_user_owned_characters(user_id)
    
    if char['id'] in owned_chars:
        await update.callback_query.answer(
            to_small_caps("⚠️ You already own this character!"),
            show_alert=True
        )
        return
    
    balance = await get_balance(user_id)
    
    rarity_emoji = RARITY_EMOJIS.get(char['rarity'], '⚪')
    rarity_name = RARITY_NAMES.get(char['rarity'], 'ᴜɴᴋɴᴏᴡɴ')
    
    safe_name = escape(str(char['name']))
    safe_anime = escape(str(char['anime']))
    
    message = f"<b>💰 {to_small_caps('Purchase Confirmation')}</b>\n\n"
    message += f"🎭 {to_small_caps('Name')}: {to_small_caps(safe_name)}\n"
    message += f"📺 {to_small_caps('Anime')}: {to_small_caps(safe_anime)}\n"
    message += f"🆔 {to_small_caps('Id')}: {char['id']}\n"
    message += f"✨ {to_small_caps('Rarity')}: {rarity_emoji} {rarity_name}\n\n"
    message += f"💸 {to_small_caps('Original Price')}: {char['base_price']:,}\n"
    message += f"🛒 {to_small_caps('Discount')}: {char['discount_percent']}%\n"
    message += f"🏷️ {to_small_caps('Final Price')}: <b>{char['final_price']:,}</b>\n\n"
    message += f"💰 {to_small_caps('Your Balance')}: {balance:,}\n\n"
    
    if balance >= char['final_price']:
        message += to_small_caps("✅ Confirm your purchase?")
    else:
        message += to_small_caps("⚠️ Insufficient balance!")
    
    keyboard = []
    
    if balance >= char['final_price']:
        keyboard.append([
            InlineKeyboardButton(
                f"✅ {to_small_caps('Confirm')}",
                callback_data=f"shop_confirm_purchase:{user_id}:{index}"
            ),
            InlineKeyboardButton(
                f"❌ {to_small_caps('Cancel')}",
                callback_data=f"shop_cancel_purchase:{user_id}:{index}"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                f"⬅️ {to_small_caps('Back')}",
                callback_data=f"shop_cancel_purchase:{user_id}:{index}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    query = update.callback_query
    photo_url = char.get('img_url')
    
    if photo_url:
        try:
            new_media = InputMediaPhoto(media=photo_url, caption=message, parse_mode='HTML')
            await query.edit_message_media(media=new_media, reply_markup=reply_markup)
        except:
            try:
                await query.delete_message()
                await query.message.reply_photo(
                    photo=photo_url,
                    caption=message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except:
                await query.edit_message_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
    else:
        try:
            if query.message.photo:
                await query.delete_message()
                await query.message.reply_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        except:
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )


async def process_purchase(update: Update, context: CallbackContext,
                           user_id: int, index: int) -> None:
    query = update.callback_query
    
    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])
    
    if index >= len(characters):
        await query.answer(to_small_caps("⚠️ Character not found!"), show_alert=True)
        return
    
    char = characters[index]
    owned_chars = await get_user_owned_characters(user_id)
    
    if char['id'] in owned_chars:
        await query.answer(
            to_small_caps("⚠️ You already own this character!"),
            show_alert=True
        )
        return
    
    balance = await get_balance(user_id)
    if balance < char['final_price']:
        await query.answer(
            to_small_caps(f"⚠️ Insufficient balance! Need {char['final_price']:,} coins"),
            show_alert=True
        )
        return
    
    full_char = await collection.find_one({'id': char['id']})
    if not full_char:
        await query.answer(to_small_caps("⚠️ Character not found in database!"), show_alert=True)
        return
    
    new_balance = await change_balance(user_id, -char['final_price'])
    success = await add_character_to_user(user_id, full_char)
    
    if success:
        safe_name = escape(str(char['name']))
        
        success_msg = f"<b>✅ {to_small_caps('Purchase Successful!')}</b>\n\n"
        success_msg += f"🎉 {to_small_caps('You got')}: {to_small_caps(safe_name)}\n"
        success_msg += f"💸 {to_small_caps('Price')}: {char['final_price']:,}\n"
        success_msg += f"💰 {to_small_caps('New Balance')}: {new_balance:,}\n"
        
        keyboard = [[
            InlineKeyboardButton(
                f"⬅️ {to_small_caps('Back to Shop')}",
                callback_data=f"shop_nav:{user_id}:{index}"
            )
        ]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if query.message.photo:
                await query.edit_message_caption(
                    caption=success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        except Exception as e:
            try:
                await query.message.delete()
                await query.message.reply_text(
                    success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except:
                pass
        
        await query.answer(to_small_caps("✅ Purchase successful!"), show_alert=True)
    else:
        await change_balance(user_id, char['final_price'])
        await query.answer(
            to_small_caps("⚠️ Purchase failed! Amount refunded."),
            show_alert=True
        )


application.add_handler(CommandHandler("shop", shop_command, block=False))
application.add_handler(CallbackQueryHandler(shop_callback, pattern='^shop_', block=False))
