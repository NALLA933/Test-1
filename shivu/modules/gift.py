from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import time
import asyncio
import logging

from shivu import user_collection, shivuu

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pending_trades = {}
pending_gifts = {}

user_locks = {}

last_trade_time = {}
last_gift_time = {}

TRADE_COOLDOWN = 60
GIFT_COOLDOWN = 30
PENDING_EXPIRY = 300
GIFT_CONFIRM_TIMEOUT = 30
MAX_INVENTORY_SIZE = 5000

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
    text = str(text) if text is not None else 'Unknown'
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

async def is_bot_or_channel(client, user_id):
    try:
        chat = await client.get_chat(user_id)
        if hasattr(chat, 'type'):
            if chat.type in ['channel', 'group', 'supergroup']:
                return True, "channel/group"
        try:
            user = await client.get_users(user_id)
            if user.is_bot:
                return True, "bot"
        except:
            pass
        return False, None
    except Exception as e:
        logger.error(f"Error checking if user {user_id} is bot/channel: {e}")
        return False, None

async def safe_store_recovery(character, context):
    try:
        recovery_collection = user_collection.database['character_recovery']
        await recovery_collection.insert_one({
            'character': character,
            'context': context,
            'timestamp': time.time(),
            'recovered': False
        })
        logger.critical(f"Character stored in recovery: {context}")
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to store character in recovery: {e}")

async def atomic_transfer_character(sender_id, receiver_id, character_id):
    sender = await user_collection.find_one({'id': sender_id})
    if not sender:
        return False, "Sender not found"
    
    character = next(
        (char for char in sender.get('characters', []) if char.get('id') == character_id), 
        None
    )
    
    if not character:
        return False, "Character not found in sender inventory"
    
    receiver = await user_collection.find_one({'id': receiver_id})
    receiver_inventory_size = len(receiver.get('characters', [])) if receiver else 0
    
    if receiver_inventory_size >= MAX_INVENTORY_SIZE:
        return False, "Receiver inventory is full"
    
    pull_result = await user_collection.update_one(
        {'id': sender_id, 'characters.id': character_id},
        {'$pull': {'characters': {'id': character_id}}}
    )
    
    if pull_result.modified_count == 0:
        return False, "Failed to remove character from sender"
    
    try:
        if receiver:
            push_result = await user_collection.update_one(
                {'id': receiver_id}, 
                {'$push': {'characters': character}}
            )
            
            if push_result.modified_count == 0:
                rollback_result = await user_collection.update_one(
                    {'id': sender_id},
                    {'$push': {'characters': character}}
                )
                
                if rollback_result.modified_count == 0:
                    await safe_store_recovery(character, f"Failed rollback: sender={sender_id}, receiver={receiver_id}")
                    logger.critical(f"CRITICAL: Rollback failed for character transfer")
                
                return False, "Failed to add character to receiver"
        else:
            insert_result = await user_collection.insert_one({
                'id': receiver_id,
                'username': receiver.get('username') if receiver else None,
                'first_name': receiver.get('first_name') if receiver else None,
                'characters': [character],
            })
            
            if not insert_result.inserted_id:
                rollback_result = await user_collection.update_one(
                    {'id': sender_id},
                    {'$push': {'characters': character}}
                )
                
                if rollback_result.modified_count == 0:
                    await safe_store_recovery(character, f"Failed rollback after insert: sender={sender_id}, receiver={receiver_id}")
                    logger.critical(f"CRITICAL: Rollback failed after insert failure")
                
                return False, "Failed to create receiver document"
        
        return True, "Transfer successful"
        
    except Exception as e:
        logger.error(f"Exception during transfer, attempting rollback: {e}")
        
        rollback_result = await user_collection.update_one(
            {'id': sender_id},
            {'$push': {'characters': character}}
        )
        
        if rollback_result.modified_count == 0:
            await safe_store_recovery(character, f"Exception rollback failed: sender={sender_id}, receiver={receiver_id}, error={str(e)}")
            logger.critical(f"CRITICAL: Exception rollback failed")
        
        return False, f"Transfer failed: {str(e)}"

async def cleanup_expired_operations():
    current_time = time.time()

    expired_trades = [k for k, v in pending_trades.items() 
                      if current_time - v['timestamp'] > PENDING_EXPIRY]
    for key in expired_trades:
        sender_id = key[0]
        if sender_id in last_trade_time:
            trade_data = pending_trades[key]
            if current_time - trade_data['timestamp'] > PENDING_EXPIRY:
                if last_trade_time.get(sender_id) == trade_data['timestamp']:
                    del last_trade_time[sender_id]
        del pending_trades[key]
        logger.info(f"Cleaned expired trade: {key}")

    expired_gifts = [k for k, v in pending_gifts.items() 
                     if current_time - v['timestamp'] > GIFT_CONFIRM_TIMEOUT]
    for key in expired_gifts:
        sender_id = key[0]
        gift_data = pending_gifts[key]
        if sender_id in last_gift_time:
            if last_gift_time.get(sender_id) == gift_data['timestamp']:
                del last_gift_time[sender_id]
        del pending_gifts[key]
        logger.info(f"Cleaned expired gift: {key} and removed cooldown")

async def auto_cleanup_task():
    while True:
        try:
            await asyncio.sleep(60)
            await cleanup_expired_operations()
        except Exception as e:
            logger.error(f"Error in auto cleanup task: {e}")

cleanup_task = None

async def start_cleanup_task():
    global cleanup_task
    if cleanup_task is None:
        cleanup_task = asyncio.create_task(auto_cleanup_task())
        logger.info("Background cleanup task started")

def check_cooldown(user_id, cooldown_dict, cooldown_time):
    current_time = time.time()
    if user_id in cooldown_dict:
        time_passed = current_time - cooldown_dict[user_id]
        if time_passed < cooldown_time:
            remaining = int(cooldown_time - time_passed)
            return False, remaining
    return True, 0

def format_character_info(character):
    name = character.get('name', 'Unknown')
    rarity = character.get('rarity', 'Unknown')
    anime = character.get('anime', 'Unknown')
    return f"**{name}**\n⭐ Rarity: {rarity}\n📺 Anime: {anime}"

def format_premium_gift_card(character, sender_name):
    name = character.get('name', 'Unknown')
    anime = character.get('anime', 'Unknown')
    char_id = character.get('id', 'Unknown')
    rarity = character.get('rarity', 'Unknown')

    if isinstance(rarity, int) and rarity in RARITY_MAP:
        rarity_display = RARITY_MAP[rarity]
    elif isinstance(rarity, str):
        rarity_display = to_small_caps(rarity)
    else:
        rarity_display = to_small_caps(str(rarity))

    name_sc = to_small_caps(name)
    anime_sc = to_small_caps(anime)
    char_id_sc = to_small_caps(char_id)

    card = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎁 {to_small_caps('gift card')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✨ {to_small_caps('name')}   : **{name_sc}**\n"
        f"🎬 {to_small_caps('anime')}  : **{anime_sc}**\n"
        f"🆔 {to_small_caps('id')}     : `{char_id_sc}`\n"
        f"⭐ {to_small_caps('rarity')} : {rarity_display}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 {to_small_caps('premium gift from')} **{sender_name}**"
    )
    return card


@shivuu.on_message(filters.command("trade"))
async def trade(client, message):
    await start_cleanup_task()

    sender_id = message.from_user.id

    await cleanup_expired_operations()

    if not message.reply_to_message:
        await message.reply_text("❌ You need to reply to a user's message to trade a character!")
        return

    receiver_id = message.reply_to_message.from_user.id
    receiver_mention = message.reply_to_message.from_user.mention

    if sender_id == receiver_id:
        await message.reply_text("❌ You can't trade a character with yourself!")
        return

    is_invalid, invalid_type = await is_bot_or_channel(client, receiver_id)
    if is_invalid:
        if invalid_type == "bot":
            await message.reply_text("❌ You can't trade a character with a bot!")
        else:
            await message.reply_text("❌ You can't trade a character with a channel or group!")
        return

    can_trade, remaining = check_cooldown(sender_id, last_trade_time, TRADE_COOLDOWN)
    if not can_trade:
        await message.reply_text(f"⏰ Please wait {remaining} seconds before initiating another trade!")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.reply_text(
            "❌ Invalid format! Usage:\n"
            "/trade <your_character_id> <their_character_id>\n\n"
            "Reply to the user's message you want to trade with."
        )
        return

    try:
        sender_char_id = args[1]
        receiver_char_id = args[2]
    except (IndexError, ValueError):
        await message.reply_text("❌ Please provide valid character IDs!")
        return

    try:
        async with get_user_lock(sender_id):
            sender = await user_collection.find_one({'id': sender_id})

            if not sender:
                await message.reply_text("❌ You don't have any characters yet!")
                return

            sender_character = next(
                (char for char in sender.get('characters', []) if char.get('id') == sender_char_id), 
                None
            )

            if not sender_character:
                await message.reply_text(f"❌ You don't have a character with ID {sender_char_id}!")
                return

        async with get_user_lock(receiver_id):
            receiver = await user_collection.find_one({'id': receiver_id})

            if not receiver:
                await message.reply_text(f"❌ {receiver_mention} doesn't have any characters yet!")
                return

            receiver_character = next(
                (char for char in receiver.get('characters', []) if char.get('id') == receiver_char_id), 
                None
            )

            if not receiver_character:
                await message.reply_text(
                    f"❌ {receiver_mention} doesn't have a character with ID {receiver_char_id}!"
                )
                return

        if (sender_id, receiver_id) in pending_trades:
            await message.reply_text("❌ You already have a pending trade with this user!")
            return

        pending_trades[(sender_id, receiver_id)] = {
            'chars': (sender_char_id, receiver_char_id),
            'timestamp': time.time()
        }

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Accept Trade", callback_data=f"accept_trade:{sender_id}:{receiver_id}")],
            [InlineKeyboardButton("❌ Decline Trade", callback_data=f"decline_trade:{sender_id}:{receiver_id}")]
        ])

        trade_msg = (
            f"🔄 **Trade Request**\n\n"
            f"**{message.from_user.first_name}** wants to trade:\n"
            f"{format_character_info(sender_character)}\n\n"
            f"For your:\n"
            f"{format_character_info(receiver_character)}\n\n"
            f"{receiver_mention}, please confirm or decline this trade."
        )

        await message.reply_text(trade_msg, reply_markup=keyboard)

        last_trade_time[sender_id] = time.time()

    except Exception as e:
        logger.error(f"Error in trade command: {e}")
        await message.reply_text("❌ An error occurred while processing the trade. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^(accept_trade|decline_trade):(\d+):(\d+)$"))
async def on_trade_callback(client, callback_query):
    data_parts = callback_query.data.split(":")
    action = data_parts[0]
    sender_id = int(data_parts[1])
    receiver_id = int(data_parts[2])

    clicker_id = callback_query.from_user.id

    if clicker_id != receiver_id:
        await callback_query.answer("❌ This trade is not for you!", show_alert=True)
        return

    trade_key = (sender_id, receiver_id)

    if trade_key not in pending_trades:
        await callback_query.answer("❌ This trade has expired or doesn't exist!", show_alert=True)
        return

    trade_data = pending_trades[trade_key]

    if time.time() - trade_data['timestamp'] > PENDING_EXPIRY:
        del pending_trades[trade_key]
        await callback_query.message.edit_text("❌ This trade request has expired!")
        return

    if action == "accept_trade":
        try:
            sender_char_id, receiver_char_id = trade_data['chars']

            try:
                await callback_query.message.edit_reply_markup(reply_markup=None)
            except:
                pass

            del pending_trades[trade_key]

            first_id, second_id = sorted([sender_id, receiver_id])
            async with get_user_lock(first_id):
                async with get_user_lock(second_id):
                    sender = await user_collection.find_one({'id': sender_id})
                    receiver = await user_collection.find_one({'id': receiver_id})

                    sender_character = next(
                        (char for char in sender.get('characters', []) if char.get('id') == sender_char_id), 
                        None
                    )

                    receiver_character = next(
                        (char for char in receiver.get('characters', []) if char.get('id') == receiver_char_id), 
                        None
                    )

                    if not sender_character:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! The sender's character no longer exists."
                        )
                        return

                    if not receiver_character:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Your character no longer exists."
                        )
                        return

                    sender_inventory_size = len(sender.get('characters', []))
                    receiver_inventory_size = len(receiver.get('characters', []))
                    
                    if sender_inventory_size - 1 + 1 > MAX_INVENTORY_SIZE:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Sender's inventory would exceed the limit."
                        )
                        return
                    
                    if receiver_inventory_size - 1 + 1 > MAX_INVENTORY_SIZE:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Your inventory would exceed the limit."
                        )
                        return

                    sender_pull = await user_collection.update_one(
                        {'id': sender_id, 'characters.id': sender_char_id},
                        {'$pull': {'characters': {'id': sender_char_id}}}
                    )

                    if sender_pull.modified_count == 0:
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Could not remove sender's character."
                        )
                        return

                    receiver_pull = await user_collection.update_one(
                        {'id': receiver_id, 'characters.id': receiver_char_id},
                        {'$pull': {'characters': {'id': receiver_char_id}}}
                    )

                    if receiver_pull.modified_count == 0:
                        await user_collection.update_one(
                            {'id': sender_id},
                            {'$push': {'characters': sender_character}}
                        )
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Could not remove your character."
                        )
                        return

                    sender_push = await user_collection.update_one(
                        {'id': sender_id},
                        {'$push': {'characters': receiver_character}}
                    )

                    if sender_push.modified_count == 0:
                        await user_collection.update_one(
                            {'id': sender_id},
                            {'$push': {'characters': sender_character}}
                        )
                        await user_collection.update_one(
                            {'id': receiver_id},
                            {'$push': {'characters': receiver_character}}
                        )
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Could not complete transfer to sender."
                        )
                        logger.error(f"Trade partial failure: rolled back sender")
                        return

                    receiver_push = await user_collection.update_one(
                        {'id': receiver_id},
                        {'$push': {'characters': sender_character}}
                    )

                    if receiver_push.modified_count == 0:
                        await user_collection.update_one(
                            {'id': sender_id, 'characters.id': receiver_char_id},
                            {'$pull': {'characters': {'id': receiver_char_id}}}
                        )
                        await user_collection.update_one(
                            {'id': sender_id},
                            {'$push': {'characters': sender_character}}
                        )
                        await user_collection.update_one(
                            {'id': receiver_id},
                            {'$push': {'characters': receiver_character}}
                        )
                        await callback_query.message.edit_text(
                            "❌ Trade failed! Could not complete transfer to receiver."
                        )
                        logger.error(f"Trade partial failure: rolled back receiver")
                        return

                    success_msg = (
                        f"✅ **Trade Successful!**\n\n"
                        f"**{callback_query.message.reply_to_message.from_user.first_name}** received:\n"
                        f"{format_character_info(receiver_character)}\n\n"
                        f"**{callback_query.from_user.first_name}** received:\n"
                        f"{format_character_info(sender_character)}"
                    )

                    await callback_query.message.edit_text(success_msg)
                    await callback_query.answer("✅ Trade completed!", show_alert=True)

                    logger.info(f"Trade completed: {sender_id} <-> {receiver_id}")

        except Exception as e:
            logger.error(f"Error accepting trade: {e}")
            await callback_query.answer("❌ Error processing trade!", show_alert=True)

    elif action == "decline_trade":
        del pending_trades[trade_key]

        await callback_query.message.edit_text(
            "❌ **Trade Declined**\n\n"
            f"{callback_query.from_user.first_name} has declined the trade."
        )
        await callback_query.answer("Trade declined!", show_alert=False)

        logger.info(f"Trade declined: {sender_id} -> {receiver_id}")


@shivuu.on_message(filters.command("gift"))
async def gift(client, message):
    await start_cleanup_task()

    sender_id = message.from_user.id

    await cleanup_expired_operations()

    if not message.reply_to_message:
        await message.reply_text("❌ You need to reply to a user's message to gift a character!")
        return

    receiver_id = message.reply_to_message.from_user.id
    receiver_mention = message.reply_to_message.from_user.mention

    if sender_id == receiver_id:
        await message.reply_text("❌ You can't gift a character to yourself!")
        return

    is_invalid, invalid_type = await is_bot_or_channel(client, receiver_id)
    if is_invalid:
        if invalid_type == "bot":
            await message.reply_text("❌ You can't gift a character to a bot!")
        else:
            await message.reply_text("❌ You can't gift a character to a channel or group!")
        return

    can_gift, remaining = check_cooldown(sender_id, last_gift_time, GIFT_COOLDOWN)
    if not can_gift:
        await message.reply_text(f"⏰ Please wait {remaining} seconds before gifting another character!")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text(
            "❌ Invalid format! Usage:\n"
            "/gift <character_id>\n\n"
            "Reply to the user's message you want to gift to."
        )
        return

    try:
        character_id = args[1]
    except (IndexError, ValueError):
        await message.reply_text("❌ Please provide a valid character ID!")
        return

    try:
        async with get_user_lock(sender_id):
            sender = await user_collection.find_one({'id': sender_id})

            if not sender:
                await message.reply_text("❌ You don't have any characters yet!")
                return

            character = next(
                (char for char in sender.get('characters', []) if char.get('id') == character_id), 
                None
            )

            if not character:
                await message.reply_text(f"❌ You don't have a character with ID {character_id}!")
                return

            receiver_username = message.reply_to_message.from_user.username
            receiver_first_name = message.reply_to_message.from_user.first_name
            sender_name = message.from_user.first_name

            if (sender_id, receiver_id) in pending_gifts:
                await message.reply_text("❌ You already have a pending gift for this user!")
                return

            pending_gifts[(sender_id, receiver_id)] = {
                'character': character,
                'receiver_username': receiver_username,
                'receiver_first_name': receiver_first_name,
                'timestamp': time.time()
            }

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm Gift", callback_data=f"confirm_gift:{sender_id}:{receiver_id}")],
                [InlineKeyboardButton("❌ Cancel Gift", callback_data=f"cancel_gift:{sender_id}:{receiver_id}")]
            ])

            gift_card = format_premium_gift_card(character, sender_name)

            gift_msg = (
                f"{gift_card}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Are you sure you want to gift this to {receiver_mention}?\n\n"
                f"⏰ {to_small_caps('you have 30 seconds to confirm')}"
            )

            await message.reply_text(gift_msg, reply_markup=keyboard)

            last_gift_time[sender_id] = time.time()

    except Exception as e:
        logger.error(f"Error in gift command: {e}")
        await message.reply_text("❌ An error occurred while processing the gift. Please try again!")


@shivuu.on_callback_query(filters.regex(r"^(confirm_gift|cancel_gift):(\d+):(\d+)$"))
async def on_gift_callback(client, callback_query):
    data_parts = callback_query.data.split(":")
    action = data_parts[0]
    sender_id = int(data_parts[1])
    receiver_id = int(data_parts[2])

    clicker_id = callback_query.from_user.id

    if clicker_id != sender_id:
        await callback_query.answer("❌ This action is not for you!", show_alert=True)
        return

    gift_key = (sender_id, receiver_id)

    if gift_key not in pending_gifts:
        await callback_query.answer("❌ This gift has expired or doesn't exist!", show_alert=True)
        return

    gift_data = pending_gifts[gift_key]

    if time.time() - gift_data['timestamp'] > GIFT_CONFIRM_TIMEOUT:
        if sender_id in last_gift_time:
            if last_gift_time.get(sender_id) == gift_data['timestamp']:
                del last_gift_time[sender_id]
        del pending_gifts[gift_key]
        await callback_query.message.edit_text(
            "❌ This gift request has expired!\n\n"
            "You can now send a new gift."
        )
        return

    if action == "confirm_gift":
        try:
            character = gift_data['character']

            try:
                await callback_query.message.edit_reply_markup(reply_markup=None)
            except:
                pass

            del pending_gifts[gift_key]

            first_id, second_id = sorted([sender_id, receiver_id])
            async with get_user_lock(first_id):
                async with get_user_lock(second_id):
                    sender = await user_collection.find_one({'id': sender_id})

                    sender_character = next(
                        (char for char in sender.get('characters', []) if char.get('id') == character['id']), 
                        None
                    )

                    if not sender_character:
                        await callback_query.message.edit_text(
                            "❌ Gift failed! The character no longer exists in your collection."
                        )
                        return

                    receiver = await user_collection.find_one({'id': receiver_id})
                    receiver_inventory_size = len(receiver.get('characters', [])) if receiver else 0
                    
                    if receiver_inventory_size >= MAX_INVENTORY_SIZE:
                        await callback_query.message.edit_text(
                            "❌ Gift failed! Receiver's inventory is full."
                        )
                        return

                    success, message_text = await atomic_transfer_character(sender_id, receiver_id, character['id'])
                    
                    if not success:
                        await callback_query.message.edit_text(
                            f"❌ Gift failed! {message_text}"
                        )
                        return

                    if not receiver:
                        receiver_update = await user_collection.update_one(
                            {'id': receiver_id},
                            {
                                '$set': {
                                    'username': gift_data['receiver_username'],
                                    'first_name': gift_data['receiver_first_name']
                                }
                            }
                        )

                    char_name = character.get('name', 'Unknown')
                    char_name_sc = to_small_caps(char_name)

                    success_msg = (
                        f"🎉 **{to_small_caps('gift successful')}**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"💝 **{char_name_sc}** {to_small_caps('has been sent')}\n"
                        f"{to_small_caps('to')} **{gift_data['receiver_first_name']}**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"✨ {to_small_caps('thank you for being generous')}"
                    )

                    await callback_query.message.edit_text(success_msg)
                    await callback_query.answer("✅ Gift sent successfully!", show_alert=True)

                    logger.info(f"Gift completed: {sender_id} -> {receiver_id}")

        except Exception as e:
            logger.error(f"Error confirming gift: {e}")
            await callback_query.answer("❌ Error processing gift!", show_alert=True)

    elif action == "cancel_gift":
        del pending_gifts[gift_key]

        await callback_query.message.edit_text(
            "❌ **Gift Cancelled**\n\n"
            "The gift has been cancelled."
        )
        await callback_query.answer("Gift cancelled!", show_alert=False)

        logger.info(f"Gift cancelled: {sender_id} -> {receiver_id}")


@shivuu.on_message(filters.command("pending"))
async def check_pending(client, message):
    user_id = message.from_user.id

    await cleanup_expired_operations()

    user_trades = []
    user_gifts = []

    for (sender_id, receiver_id), data in pending_trades.items():
        if sender_id == user_id:
            user_trades.append(f"• Trade as sender (waiting for receiver)")
        elif receiver_id == user_id:
            user_trades.append(f"• Trade as receiver (pending your confirmation)")

    for (sender_id, receiver_id), data in pending_gifts.items():
        if sender_id == user_id:
            user_gifts.append(f"• Gift (pending your confirmation)")

    if not user_trades and not user_gifts:
        await message.reply_text("✅ You have no pending trades or gifts!")
        return

    msg = "📋 **Your Pending Operations:**\n\n"

    if user_trades:
        msg += "**Trades:**\n" + "\n".join(user_trades) + "\n\n"

    if user_gifts:
        msg += "**Gifts:**\n" + "\n".join(user_gifts)

    await message.reply_text(msg)


ADMIN_USER_IDS = [123456789, 987654321]

@shivuu.on_message(filters.command("clearpending") & filters.user(ADMIN_USER_IDS))
async def clear_pending(client, message):
    pending_trades.clear()
    pending_gifts.clear()
    last_trade_time.clear()
    last_gift_time.clear()
    await message.reply_text("✅ All pending operations and cooldowns have been cleared!")
