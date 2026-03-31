import random
import time
from datetime import datetime, timezone, timedelta
from html import escape
from typing import List, Dict, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler

from shivu import collection, user_collection, application
from shivu.character_ids import (
    character_id_query,
    normalize_character_document,
    normalize_character_id,
)
from shivu.security import is_owner_or_sudo

# Small Caps Conversion Utility
def to_small_caps(text: str) -> str:
    """Convert standard text to Small Caps font."""
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


# Rarity Emoji Mapping
RARITY_EMOJIS = {
    1: '⚪', 2: '🔵', 3: '🟡', 4: '💮', 5: '👹',
    6: '🎐', 7: '🔮', 8: '🪐', 9: '⚰️', 10: '🌬️',
    11: '💝', 12: '🌸', 13: '🏖️', 14: '🍭', 15: '🧬'
}

# Rarity Names
RARITY_NAMES = {
    1: "ᴄᴏᴍᴍᴏɴ", 2: "ʀᴀʀᴇ", 3: "ʟᴇɢᴇɴᴅᴀʀʏ", 4: "ꜱᴘᴇᴄɪᴀʟ", 5: "ᴀɴᴄɪᴇɴᴛ",
    6: "ᴄᴇʟᴇꜱᴛɪᴀʟ", 7: "ᴇᴘɪᴄ", 8: "ᴄᴏꜱᴍɪᴄ", 9: "ɴɪɢʜᴛᴍᴀʀᴇ", 10: "ꜰʀᴏꜱᴛʙᴏʀɴ",
    11: "ᴠᴀʟᴇɴᴛɪɴᴇ", 12: "ꜱᴘʀɪɴɢ", 13: "ᴛʀᴏᴘɪᴄᴀʟ", 14: "ᴋᴀᴡᴀɪɪ", 15: "ʜʏʙʀɪᴅ"
}

# Shop Configuration
SHOP_RARITIES = [4, 5, 6, 14]  # Special, Ancient, Celestial, Kawaii

# Price Ranges for each rarity
PRICE_RANGES = {
    4: (400000, 500000),   # Special
    5: (600000, 700000),   # Ancient
    6: (650000, 750000),   # Celestial
    14: (450000, 550000),  # Kawaii
}

# Discount range (5-15%)
DISCOUNT_MIN = 5
DISCOUNT_MAX = 15

# Refresh cost
REFRESH_COST = 20000

# India timezone offset (IST = UTC+5:30)
IST_OFFSET = timedelta(hours=5, minutes=30)


def get_rarity_from_string(rarity_value) -> int:
    if isinstance(rarity_value, int):
        return rarity_value

    if isinstance(rarity_value, str):
        rarity_str = rarity_value.strip().lower()

        if rarity_str.isdigit():
            return int(rarity_str)

        emoji_to_int = {
            '⚪': 1, '🔵': 2, '🟡': 3, '💮': 4, '👹': 5,
            '🎐': 6, '🔮': 7, '🪐': 8, '⚰️': 9, '🌬️': 10,
            '💝': 11, '🌸': 12, '🏖️': 13, '🍭': 14, '🧬': 15
        }

        for emoji, num in emoji_to_int.items():
            if emoji in rarity_str:
                return num

        name_to_int = {
            'common': 1, 'rare': 2, 'legendary': 3, 'special': 4, 'ancient': 5,
            'celestial': 6, 'epic': 7, 'cosmic': 8, 'nightmare': 9, 'frostborn': 10,
            'valentine': 11, 'spring': 12, 'tropical': 13, 'kawaii': 14, 'hybrid': 15,
        }

        if rarity_str in name_to_int:
            return name_to_int[rarity_str]

    return 1


def get_ist_midnight() -> datetime:
    """Get the next midnight in IST timezone."""
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFFSET

    # Get next midnight IST
    next_midnight_ist = (now_ist + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Convert back to UTC
    next_midnight_utc = next_midnight_ist - IST_OFFSET
    return next_midnight_utc


async def get_balance(user_id: int) -> int:
    """Get user's balance from user_collection."""
    user = await user_collection.find_one({'id': user_id})
    if not user:
        return 0
    return int(user.get('balance', 0))


async def change_balance(user_id: int, amount: int) -> int:
    """Change user's balance atomically."""
    await user_collection.update_one(
        {"id": user_id},
        {"$inc": {"balance": int(amount)}},
        upsert=True
    )
    user = await user_collection.find_one({'id': user_id})
    return int(user.get('balance', 0)) if user else 0


async def get_user_owned_characters(user_id: int) -> List[int]:
    """Get list of character IDs owned by user."""
    user = await user_collection.find_one({'id': user_id})
    if not user:
        return []

    characters = user.get('characters', [])
    owned_ids = [
        normalize_character_id(char.get('id'))
        for char in characters
        if normalize_character_id(char.get('id')) is not None
    ]
    return list(set(owned_ids))  # Return unique IDs


async def get_character_owner_count(char_id: int) -> int:
    """Get count of how many users own this character."""
    count = await user_collection.count_documents(character_id_query(char_id, 'characters.id'))
    return count


async def add_character_to_user(user_id: int, character: dict) -> bool:
    """Add a character to user's collection."""
    try:
        char_data = normalize_character_document({
            'id': character['id'],
            'name': character['name'],
            'anime': character['anime'],
            'rarity': character.get('rarity', 1),
            'img_url': character.get('img_url', '')
        })

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


async def fetch_shop_characters() -> List[dict]:
    """Fetch all eligible characters for shop (handles string/emoji rarities)."""
    all_chars = []
    async for char in collection.find({}):
        rarity_val = char.get('rarity', 1)
        rarity_int = get_rarity_from_string(rarity_val)

        if rarity_int in SHOP_RARITIES:
            char['rarity'] = rarity_int
            char = normalize_character_document(char)
            all_chars.append(char)

    return all_chars


async def get_shop_data(user_id: int) -> dict:
    """Get or create shop data for user."""
    user = await user_collection.find_one({'id': user_id})

    if not user:
        # Create new user with shop data
        shop_data = await initialize_shop_data(user_id)
        return shop_data

    shop_data = user.get('shop_data', {})

    # Check if shop needs reset (daily at midnight IST)
    last_reset = shop_data.get('last_reset', 0)
    next_reset = get_ist_midnight().timestamp()

    current_time = time.time()

    # If last reset was before the last midnight, reset shop
    if last_reset < (current_time - 86400):  # More than 24 hours
        shop_data = await initialize_shop_data(user_id)

    return shop_data


async def initialize_shop_data(user_id: int) -> dict:
    """Initialize new shop data for user."""
    characters = []

    eligible_chars = await fetch_shop_characters()

    if len(eligible_chars) >= 3:
        selected_chars = random.sample(eligible_chars, 3)
    else:
        selected_chars = eligible_chars

    for char in selected_chars:
        rarity = char.get('rarity', 4)
        rarity = get_rarity_from_string(rarity)

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
    """Refresh shop characters (once per day)."""
    user = await user_collection.find_one({'id': user_id})

    if not user:
        return False, to_small_caps("Error: User not found")

    shop_data = user.get('shop_data', {})

    # Check if already refreshed
    if shop_data.get('refresh_used', False):
        return False, to_small_caps("⚠️ You have reached daily limit of 1 refresh!")

    # Check balance
    balance = await get_balance(user_id)
    if balance < REFRESH_COST:
        return False, to_small_caps(f"⚠️ Insufficient balance! Need {REFRESH_COST:,} coins")

    # Deduct refresh cost
    await change_balance(user_id, -REFRESH_COST)

    # Generate new characters
    characters = []
    pipeline = [
        {'$match': {'rarity': {'$in': SHOP_RARITIES}}},
        {'$sample': {'size': 3}}
    ]

    async for char in collection.aggregate(pipeline):
        rarity = char.get('rarity', 4)
        rarity = get_rarity_from_string(rarity)

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

    # Update shop data
    shop_data['characters'] = characters
    shop_data['refresh_used'] = True
    shop_data['current_index'] = 0

    await user_collection.update_one(
        {'id': user_id},
        {'$set': {'shop_data': shop_data}}
    )

    return True, to_small_caps(f"✅ Shop refreshed! Cost: {REFRESH_COST:,} coins")


async def shop_command(update: Update, context: CallbackContext) -> None:
    """Handle /shop command - Display normal shop."""
    user_id = update.effective_user.id

    # Get shop data
    shop_data = await get_shop_data(user_id)

    # Display first character
    await display_shop_character(update, context, user_id, 0)


async def display_shop_character(update: Update, context: CallbackContext, 
                                 user_id: int, index: int) -> None:
    """Display a specific character from shop."""
    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])

    if not characters:
        message = to_small_caps("⚠️ Shop is empty! Please try again later.")
        if update.message:
            await update.message.reply_text(message)
        else:
            try:
                await update.callback_query.edit_message_caption(caption=message)
            except:
                await update.callback_query.edit_message_text(message)
        return

    # Ensure index is valid
    index = max(0, min(index, len(characters) - 1))

    # Update current index
    await user_collection.update_one(
        {'id': user_id},
        {'$set': {'shop_data.current_index': index}}
    )

    char = characters[index]

    # Get owner count
    owner_count = await get_character_owner_count(char['id'])

    # Check if user already owns this character
    owned_chars = await get_user_owned_characters(user_id)
    status = "Sold" if normalize_character_id(char['id']) in owned_chars else "Available"

    # Build message
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

    # Build keyboard
    keyboard = []

    # Purchase button
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

    # Navigation buttons
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

    # Premium shop button
    keyboard.append([
        InlineKeyboardButton(
            f"💸 {to_small_caps('Premium Shop')}",
            callback_data=f"shop_premium:{user_id}"
        )
    ])

    # Cancel button
    keyboard.append([
        InlineKeyboardButton(
            to_small_caps("❌ Close"),
            callback_data=f"shop_close:{user_id}"
        )
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send or edit message with photo
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
        
        # FIX: Check if message has photo or text properly
        try:
            # First try to edit media (photo with caption)
            if photo_url:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo_url, caption=message, parse_mode='HTML'),
                    reply_markup=reply_markup
                )
            else:
                # No photo URL, try editing text
                # Check if original message has photo
                if query.message and query.message.photo:
                    # Message has photo but we want to remove it
                    await query.delete_message()
                    await query.message.chat.send_message(
                        text=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
                else:
                    # Message is text only
                    await query.edit_message_text(
                        text=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
        except Exception as e:
            # Fallback: delete and resend
            try:
                await query.delete_message()
                if photo_url:
                    await query.message.chat.send_photo(
                        photo=photo_url,
                        caption=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
                else:
                    await query.message.chat.send_message(
                        text=message,
                        parse_mode='HTML',
                        reply_markup=reply_markup
                    )
            except Exception as e2:
                print(f"Error in display_shop_character: {e2}")
                # Last resort: just answer the callback
                await query.answer("Error displaying shop. Please use /shop again.")


async def shop_callback(update: Update, context: CallbackContext) -> None:
    """Handle shop callback queries."""
    query = update.callback_query
    data = query.data

    if data == "shop_noop":
        await query.answer()
        return

    # Parse callback data
    parts = data.split(':')
    action = parts[0]

    if action == "shop_nav":
        # Navigation
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)

        # Check authorization
        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return

        await display_shop_character(update, context, user_id, index)
        await query.answer()

    elif action == "shop_refresh":
        # Refresh shop
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
        # Premium shop (coming soon) - just show alert
        _, user_id = parts
        user_id = int(user_id)

        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return

        # Just show alert, don't change page
        await query.answer(
            f"💸 {to_small_caps('Premium Shop')}\n\n✨ {to_small_caps('Coming Soon...')}",
            show_alert=True
        )

    elif action == "shop_purchase":
        # Show purchase confirmation
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)

        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return

        await show_purchase_confirmation(update, context, user_id, index)
        await query.answer()

    elif action == "shop_confirm_purchase":
        # Confirm purchase
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)

        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return

        await process_purchase(update, context, user_id, index)

    elif action == "shop_cancel_purchase":
        # Cancel purchase - go back to shop
        _, user_id, index = parts
        user_id = int(user_id)
        index = int(index)

        if query.from_user.id != user_id:
            await query.answer(to_small_caps("This is not your shop!"), show_alert=True)
            return

        await query.answer(to_small_caps("Purchase cancelled"))
        await display_shop_character(update, context, user_id, index)

    elif action == "shop_close":
        # Close shop
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
    """Show purchase confirmation screen."""
    query = update.callback_query
    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])

    if index >= len(characters):
        return

    char = characters[index]

    # Check if already owned
    owned_chars = await get_user_owned_characters(user_id)
    if normalize_character_id(char['id']) in owned_chars:
        await query.answer(
            to_small_caps("⚠️ You already own this character!"),
            show_alert=True
        )
        return

    # Get balance
    balance = await get_balance(user_id)

    # Build confirmation message
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

    # Build keyboard
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

    # FIX: Check if message has photo or text properly
    try:
        # Try to edit caption first (if message has photo)
        if query.message and query.message.photo:
            await query.edit_message_caption(
                caption=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            # Message is text only
            await query.edit_message_text(
                text=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    except Exception as e:
        # Fallback: delete and resend
        try:
            await query.delete_message()
            await query.message.chat.send_message(
                text=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e2:
            print(f"Error in show_purchase_confirmation: {e2}")


async def process_purchase(update: Update, context: CallbackContext,
                           user_id: int, index: int) -> None:
    """Process the actual purchase."""
    query = update.callback_query

    shop_data = await get_shop_data(user_id)
    characters = shop_data.get('characters', [])

    if index >= len(characters):
        await query.answer(to_small_caps("⚠️ Character not found!"), show_alert=True)
        return

    char = characters[index]

    # Check if already owned
    owned_chars = await get_user_owned_characters(user_id)
    if normalize_character_id(char['id']) in owned_chars:
        await query.answer(
            to_small_caps("⚠️ You already own this character!"),
            show_alert=True
        )
        return

    # Check balance
    balance = await get_balance(user_id)
    if balance < char['final_price']:
        await query.answer(
            to_small_caps(f"⚠️ Insufficient balance! Need {char['final_price']:,} coins"),
            show_alert=True
        )
        return

    # Get full character data from collection
    full_char = await collection.find_one(character_id_query(char['id']))
    if not full_char:
        await query.answer(to_small_caps("⚠️ Character not found in database!"), show_alert=True)
        return

    # Deduct balance
    new_balance = await change_balance(user_id, -char['final_price'])

    # Add character to user
    success = await add_character_to_user(user_id, full_char)

    if success:
        # Show success message
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

        # FIX: Check if message has photo or text properly
        try:
            if query.message and query.message.photo:
                # Message has photo, edit caption
                await query.edit_message_caption(
                    caption=success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                # Message is text only
                await query.edit_message_text(
                    text=success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
        except Exception as e:
            # Fallback: delete and send new message
            try:
                await query.message.delete()
                await query.message.chat.send_message(
                    text=success_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except Exception as e2:
                print(f"Error in process_purchase: {e2}")

        await query.answer(to_small_caps("✅ Purchase successful!"), show_alert=True)
    else:
        # Refund on failure
        await change_balance(user_id, char['final_price'])
        await query.answer(
            to_small_caps("⚠️ Purchase failed! Amount refunded."),
            show_alert=True
        )


async def resetshop_command(update: Update, context: CallbackContext) -> None:
    """Handle /resetshop command - Owner only."""
    user_id = update.effective_user.id

    # Check if user is owner or sudo
    if not is_owner_or_sudo(user_id):
        await update.message.reply_text(to_small_caps("⚠️ You are not authorized to use this command!"))
        return

    # Check if user_id argument is provided
    if not context.args:
        await update.message.reply_text(to_small_caps("⚠️ Please provide a user ID! Usage: /resetshop <user_id>"))
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(to_small_caps("⚠️ Invalid user ID! Please provide a valid number."))
        return

    # Reset shop for target user
    await initialize_shop_data(target_user_id)

    await update.message.reply_text(
        to_small_caps(f"✅ Shop reset successfully for user {target_user_id}!")
    )


# Register handlers
application.add_handler(CommandHandler("shop", shop_command, block=False))
application.add_handler(CommandHandler("resetshop", resetshop_command, block=False))
application.add_handler(CallbackQueryHandler(shop_callback, pattern='^shop_', block=False))
