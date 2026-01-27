from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import time
import asyncio
import logging

from shivu import user_collection, shivuu

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Storage for pending operations
pending_trades = {}  # {(sender_id, receiver_id): {'chars': (s_char_id, r_char_id), 'timestamp': time, 'message_id': int}}
pending_gifts = {}   # {(sender_id, receiver_id): {'character': char, 'receiver_info': {...}, 'timestamp': time}}

# User locks to prevent concurrent operations
user_locks = {}

# Cooldown tracking
last_trade_time = {}
last_gift_time = {}

# Configuration
TRADE_COOLDOWN = 60  # 60 seconds
GIFT_COOLDOWN = 30   # 30 seconds
PENDING_EXPIRY = 300  # 5 minutes

def get_user_lock(user_id):
    """Get or create a lock for a specific user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

async def cleanup_expired_operations():
    """Clean up expired pending trades and gifts"""
    current_time = time.time()
    
    # Clean expired trades
    expired_trades = [k for k, v in pending_trades.items() 
                      if current_time - v['timestamp'] > PENDING_EXPIRY]
    for key in expired_trades:
        del pending_trades[key]
        logger.info(f"Cleaned expired trade: {key}")
    
    # Clean expired gifts
    expired_gifts = [k for k, v in pending_gifts.items() 
                     if current_time - v['timestamp'] > PENDING_EXPIRY]
    for key in expired_gifts:
        del pending_gifts[key]
        logger.info(f"Cleaned expired gift: {key}")

def check_cooldown(user_id, cooldown_dict, cooldown_time):
    """Check if user is on cooldown"""
    current_time = time.time()
    if user_id in cooldown_dict:
        time_passed = current_time - cooldown_dict[user_id]
        if time_passed < cooldown_time:
            remaining = int(cooldown_time - time_passed)
            return False, remaining
    return True, 0

def format_character_info(character):
    """Format character information for display with premium styling"""
    name = character.get('name', 'Unknown')
    rarity = character.get('rarity', '⭐')
    anime = character.get('anime', 'Unknown')
    char_id = character.get('id', 'N/A')
    
    # Premium compact format
    return (
        f"<b>{name}</b>\n"
        f"<code>├ ID:</code> <code>{char_id}</code>\n"
        f"<code>├ ⭐ Rarity:</code> <code>{rarity}</code>\n"
        f"<code>└ 📺 Anime:</code> <code>{anime}</code>"
    )


@shivuu.on_message(filters.command("trade"))
async def trade(client, message):
    """Handle trade command"""
    sender_id = message.from_user.id
    
    # Clean expired operations
    await cleanup_expired_operations()
    
    # Check if replying to a message
    if not message.reply_to_message:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ TRADE ERROR</b>  ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Reply to a user's message</code>\n"
            "<code>   to initiate trade!</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    receiver_id = message.reply_to_message.from_user.id
    receiver_mention = message.reply_to_message.from_user.mention
    
    # Check if trading with self
    if sender_id == receiver_id:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ TRADE ERROR</b>  ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Self-trading not allowed!</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    # Check cooldown
    can_trade, remaining = check_cooldown(sender_id, last_trade_time, TRADE_COOLDOWN)
    if not can_trade:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⏳ COOLDOWN</b>    ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            f"<code>⏱️ Wait {remaining}s before trading</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    # Validate command format
    if len(message.command) != 3:
        await message.reply_text(
            "╭━━━━━━━━━━━━━━━━╮\n"
            "┃  <b>📋 TRADE FORMAT</b>  ┃\n"
            "╰━━━━━━━━━━━━━━━━╯\n\n"
            "<code>Usage:</code>\n"
            "<code>/trade [Your ID] [Their ID]</code>\n\n"
            "<code>Example:</code>\n"
            "<code>/trade char123 char456</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    sender_character_id = message.command[1]
    receiver_character_id = message.command[2]
    
    try:
        # Acquire locks for both users
        async with get_user_lock(sender_id), get_user_lock(receiver_id):
            # Fetch user data
            sender = await user_collection.find_one({'id': sender_id})
            receiver = await user_collection.find_one({'id': receiver_id})
            
            # Check if users exist
            if not sender:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NO DATA</b>    ┃\n"
                    "╰━━━━━━━━━━━━━╯\n\n"
                    "<code>❌ No characters found!</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            if not receiver:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NO DATA</b>    ┃\n"
                    "╰━━━━━━━━━━━━━╯\n\n"
                    "<code>❌ User has no characters!</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Find characters
            sender_character = next(
                (char for char in sender.get('characters', []) if char.get('id') == sender_character_id), 
                None
            )
            receiver_character = next(
                (char for char in receiver.get('characters', []) if char.get('id') == receiver_character_id), 
                None
            )
            
            # Validate characters exist
            if not sender_character:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NOT FOUND</b>    ┃\n"
                    "╰━━━━━━━━━━━━━━━╯\n\n"
                    f"<code>❌ Character ID: {sender_character_id}</code>\n"
                    f"<code>   not in your collection</code>\n\n"
                    "<code>💡 Use /collection to view</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            if not receiver_character:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NOT FOUND</b>    ┃\n"
                    "╰━━━━━━━━━━━━━━━╯\n\n"
                    f"<code>❌ Character ID: {receiver_character_id}</code>\n"
                    f"<code>   not in user's collection</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Check if already in a pending trade
            if (sender_id, receiver_id) in pending_trades or (receiver_id, sender_id) in pending_trades:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ PENDING TRADE</b> ┃\n"
                    "╰━━━━━━━━━━━━━━━━╯\n\n"
                    "<code>❌ Trade already pending</code>\n"
                    "<code>   with this user</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Store pending trade
            pending_trades[(sender_id, receiver_id)] = {
                'chars': (sender_character_id, receiver_character_id),
                'timestamp': time.time(),
                'sender_character': sender_character,
                'receiver_character': receiver_character
            }
            
            # Create compact keyboard
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Accept", callback_data=f"confirm_trade"),
                    InlineKeyboardButton("❌ Decline", callback_data=f"cancel_trade")
                ]
            ])
            
            # Send trade proposal with premium styling
            trade_msg = (
                "╭━━━━━━━━━━━━━━━━━╮\n"
                "┃  <b>🔄 TRADE PROPOSAL</b>  ┃\n"
                "╰━━━━━━━━━━━━━━━━━╯\n\n"
                f"<b>🎯 Sender:</b> {message.from_user.first_name}\n\n"
                "<b>┌─ 📤 OFFERING ─────┐</b>\n"
                f"{format_character_info(sender_character)}\n"
                "<b>└───────────────────┘</b>\n\n"
                "<b>┌─ 📥 REQUESTING ───┐</b>\n"
                f"{format_character_info(receiver_character)}\n"
                "<b>└───────────────────┘</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>👤 {receiver_mention}</b>\n"
                "<code>⚡ Accept this trade?</code>"
            )
            
            await message.reply_text(trade_msg, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            
            # Update cooldown
            last_trade_time[sender_id] = time.time()
            
    except Exception as e:
        logger.error(f"Error in trade command: {e}")
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ ERROR</b>      ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Failed to process trade</code>\n"
            "<code>💡 Please try again</code>",
            parse_mode=enums.ParseMode.HTML
        )


@shivuu.on_callback_query(filters.create(lambda _, __, query: query.data in ["confirm_trade", "cancel_trade"]))
async def on_trade_callback(client, callback_query):
    """Handle trade confirmation/cancellation"""
    receiver_id = callback_query.from_user.id
    
    # Find the pending trade for this receiver
    trade_key = None
    trade_data = None
    
    for (sender_id, _receiver_id), data in pending_trades.items():
        if _receiver_id == receiver_id:
            trade_key = (sender_id, _receiver_id)
            trade_data = data
            break
    
    # Check if trade exists
    if not trade_key:
        await callback_query.answer("❌ Trade expired or not for you!", show_alert=True)
        return
    
    sender_id = trade_key[0]
    
    # Check if trade expired
    if time.time() - trade_data['timestamp'] > PENDING_EXPIRY:
        del pending_trades[trade_key]
        await callback_query.message.edit_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⏱️ EXPIRED</b>    ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Trade request expired</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    if callback_query.data == "confirm_trade":
        try:
            sender_character_id, receiver_character_id = trade_data['chars']
            
            # Acquire locks for both users
            async with get_user_lock(sender_id), get_user_lock(receiver_id):
                # Re-fetch user data to ensure consistency
                sender = await user_collection.find_one({'id': sender_id})
                receiver = await user_collection.find_one({'id': receiver_id})
                
                # Verify characters still exist
                sender_character = next(
                    (char for char in sender.get('characters', []) if char.get('id') == sender_character_id), 
                    None
                )
                receiver_character = next(
                    (char for char in receiver.get('characters', []) if char.get('id') == receiver_character_id), 
                    None
                )
                
                if not sender_character or not receiver_character:
                    await callback_query.message.edit_text(
                        "╭━━━━━━━━━━━━━╮\n"
                        "┃  <b>⚠️ FAILED</b>     ┃\n"
                        "╰━━━━━━━━━━━━━╯\n\n"
                        "<code>❌ Character no longer exists</code>",
                        parse_mode=enums.ParseMode.HTML
                    )
                    del pending_trades[trade_key]
                    return
                
                # Remove characters from original owners
                sender['characters'].remove(sender_character)
                receiver['characters'].remove(receiver_character)
                
                # Add characters to new owners
                sender['characters'].append(receiver_character)
                receiver['characters'].append(sender_character)
                
                # Update database
                await user_collection.update_one(
                    {'id': sender_id}, 
                    {'$set': {'characters': sender['characters']}}
                )
                await user_collection.update_one(
                    {'id': receiver_id}, 
                    {'$set': {'characters': receiver['characters']}}
                )
                
                # Remove from pending
                del pending_trades[trade_key]
                
                # Success message with premium styling
                success_msg = (
                    "╭━━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>✅ SUCCESS!</b>      ┃\n"
                    "╰━━━━━━━━━━━━━━━━╯\n\n"
                    "<b>🎉 Trade Completed!</b>\n\n"
                    f"<code>👤 {callback_query.from_user.first_name}</code>\n"
                    "<code>   successfully traded</code>\n"
                    "<code>   characters!</code>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<code>✨ Enjoy your new characters!</code>"
                )
                
                await callback_query.message.edit_text(success_msg, parse_mode=enums.ParseMode.HTML)
                await callback_query.answer("✅ Trade completed!", show_alert=True)
                
                logger.info(f"Trade completed: {sender_id} <-> {receiver_id}")
                
        except Exception as e:
            logger.error(f"Error confirming trade: {e}")
            await callback_query.answer("❌ Error processing trade!", show_alert=True)
            if trade_key in pending_trades:
                del pending_trades[trade_key]
    
    elif callback_query.data == "cancel_trade":
        # Remove from pending
        del pending_trades[trade_key]
        
        await callback_query.message.edit_text(
            "╭━━━━━━━━━━━━━━━╮\n"
            "┃  <b>❌ CANCELLED</b>    ┃\n"
            "╰━━━━━━━━━━━━━━━╯\n\n"
            "<code>🚫 Trade declined by receiver</code>",
            parse_mode=enums.ParseMode.HTML
        )
        await callback_query.answer("Trade cancelled!", show_alert=False)
        
        logger.info(f"Trade cancelled: {sender_id} <-> {receiver_id}")


@shivuu.on_message(filters.command("gift"))
async def gift(client, message):
    """Handle gift command"""
    sender_id = message.from_user.id
    
    # Clean expired operations
    await cleanup_expired_operations()
    
    # Check if replying to a message
    if not message.reply_to_message:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ GIFT ERROR</b>   ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Reply to a user's message</code>\n"
            "<code>   to send a gift!</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    receiver_id = message.reply_to_message.from_user.id
    receiver_username = message.reply_to_message.from_user.username
    receiver_first_name = message.reply_to_message.from_user.first_name
    receiver_mention = message.reply_to_message.from_user.mention
    
    # Check if gifting to self
    if sender_id == receiver_id:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ GIFT ERROR</b>   ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Self-gifting not allowed!</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    # Check cooldown
    can_gift, remaining = check_cooldown(sender_id, last_gift_time, GIFT_COOLDOWN)
    if not can_gift:
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⏳ COOLDOWN</b>    ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            f"<code>⏱️ Wait {remaining}s before gifting</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    # Validate command format
    if len(message.command) != 2:
        await message.reply_text(
            "╭━━━━━━━━━━━━━━━╮\n"
            "┃  <b>📋 GIFT FORMAT</b>  ┃\n"
            "╰━━━━━━━━━━━━━━━╯\n\n"
            "<code>Usage:</code>\n"
            "<code>/gift [Character ID]</code>\n\n"
            "<code>Example:</code>\n"
            "<code>/gift char123</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    character_id = message.command[1]
    
    try:
        # Acquire lock for sender
        async with get_user_lock(sender_id):
            # Fetch sender data
            sender = await user_collection.find_one({'id': sender_id})
            
            if not sender:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NO DATA</b>    ┃\n"
                    "╰━━━━━━━━━━━━━╯\n\n"
                    "<code>❌ No characters found!</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Find character
            character = next(
                (char for char in sender.get('characters', []) if char.get('id') == character_id), 
                None
            )
            
            if not character:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ NOT FOUND</b>    ┃\n"
                    "╰━━━━━━━━━━━━━━━╯\n\n"
                    f"<code>❌ Character ID: {character_id}</code>\n"
                    f"<code>   not in your collection</code>\n\n"
                    "<code>💡 Use /collection to view</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Check if already in a pending gift
            if (sender_id, receiver_id) in pending_gifts:
                await message.reply_text(
                    "╭━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>⚠️ PENDING GIFT</b> ┃\n"
                    "╰━━━━━━━━━━━━━━━╯\n\n"
                    "<code>❌ Gift already pending</code>\n"
                    "<code>   for this user</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                return
            
            # Store pending gift
            pending_gifts[(sender_id, receiver_id)] = {
                'character': character,
                'receiver_username': receiver_username,
                'receiver_first_name': receiver_first_name,
                'timestamp': time.time()
            }
            
            # Create compact keyboard
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirm", callback_data="confirm_gift"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_gift")
                ]
            ])
            
            # Send gift confirmation with premium styling
            gift_msg = (
                "╭━━━━━━━━━━━━━━━━╮\n"
                "┃  <b>🎁 GIFT CONFIRM</b>   ┃\n"
                "╰━━━━━━━━━━━━━━━━╯\n\n"
                "<b>┌─ 📦 CHARACTER ────┐</b>\n"
                f"{format_character_info(character)}\n"
                "<b>└───────────────────┘</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>🎯 Recipient:</b> {receiver_mention}\n\n"
                "<code>⚡ Confirm this gift?</code>"
            )
            
            await message.reply_text(gift_msg, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            
            # Update cooldown
            last_gift_time[sender_id] = time.time()
            
    except Exception as e:
        logger.error(f"Error in gift command: {e}")
        await message.reply_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⚠️ ERROR</b>      ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Failed to process gift</code>\n"
            "<code>💡 Please try again</code>",
            parse_mode=enums.ParseMode.HTML
        )


@shivuu.on_callback_query(filters.create(lambda _, __, query: query.data in ["confirm_gift", "cancel_gift"]))
async def on_gift_callback(client, callback_query):
    """Handle gift confirmation/cancellation"""
    sender_id = callback_query.from_user.id
    
    # Find the pending gift for this sender
    gift_key = None
    gift_data = None
    
    for (_sender_id, receiver_id), data in pending_gifts.items():
        if _sender_id == sender_id:
            gift_key = (_sender_id, receiver_id)
            gift_data = data
            break
    
    # Check if gift exists
    if not gift_key:
        await callback_query.answer("❌ Gift expired or not found!", show_alert=True)
        return
    
    receiver_id = gift_key[1]
    
    # Check if gift expired
    if time.time() - gift_data['timestamp'] > PENDING_EXPIRY:
        del pending_gifts[gift_key]
        await callback_query.message.edit_text(
            "╭━━━━━━━━━━━━━╮\n"
            "┃  <b>⏱️ EXPIRED</b>    ┃\n"
            "╰━━━━━━━━━━━━━╯\n\n"
            "<code>❌ Gift request expired</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    if callback_query.data == "confirm_gift":
        try:
            character = gift_data['character']
            
            # Acquire locks for both users
            async with get_user_lock(sender_id), get_user_lock(receiver_id):
                # Re-fetch sender data
                sender = await user_collection.find_one({'id': sender_id})
                
                # Verify character still exists
                sender_character = next(
                    (char for char in sender.get('characters', []) if char.get('id') == character['id']), 
                    None
                )
                
                if not sender_character:
                    await callback_query.message.edit_text(
                        "╭━━━━━━━━━━━━━╮\n"
                        "┃  <b>⚠️ FAILED</b>     ┃\n"
                        "╰━━━━━━━━━━━━━╯\n\n"
                        "<code>❌ Character no longer exists</code>",
                        parse_mode=enums.ParseMode.HTML
                    )
                    del pending_gifts[gift_key]
                    return
                
                # Remove character from sender
                sender['characters'].remove(sender_character)
                await user_collection.update_one(
                    {'id': sender_id}, 
                    {'$set': {'characters': sender['characters']}}
                )
                
                # Add character to receiver
                receiver = await user_collection.find_one({'id': receiver_id})
                
                if receiver:
                    # Receiver exists, add to their collection
                    await user_collection.update_one(
                        {'id': receiver_id}, 
                        {'$push': {'characters': character}}
                    )
                else:
                    # Create new user document for receiver
                    await user_collection.insert_one({
                        'id': receiver_id,
                        'username': gift_data['receiver_username'],
                        'first_name': gift_data['receiver_first_name'],
                        'characters': [character],
                    })
                
                # Remove from pending
                del pending_gifts[gift_key]
                
                # Success message with premium styling
                char_name = character.get('name', 'Unknown')
                success_msg = (
                    "╭━━━━━━━━━━━━━━━━╮\n"
                    "┃  <b>✅ SUCCESS!</b>      ┃\n"
                    "╰━━━━━━━━━━━━━━━━╯\n\n"
                    "<b>🎁 Gift Sent!</b>\n\n"
                    f"<code>📦 Character: {char_name}</code>\n"
                    f"<code>👤 Recipient: {gift_data['receiver_first_name']}</code>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<code>✨ What a generous gift!</code>"
                )
                
                await callback_query.message.edit_text(success_msg, parse_mode=enums.ParseMode.HTML)
                await callback_query.answer("✅ Gift sent successfully!", show_alert=True)
                
                logger.info(f"Gift completed: {sender_id} -> {receiver_id}")
                
        except Exception as e:
            logger.error(f"Error confirming gift: {e}")
            await callback_query.answer("❌ Error processing gift!", show_alert=True)
            if gift_key in pending_gifts:
                del pending_gifts[gift_key]
    
    elif callback_query.data == "cancel_gift":
        # Remove from pending
        del pending_gifts[gift_key]
        
        await callback_query.message.edit_text(
            "╭━━━━━━━━━━━━━━━╮\n"
            "┃  <b>❌ CANCELLED</b>    ┃\n"
            "╰━━━━━━━━━━━━━━━╯\n\n"
            "<code>🚫 Gift cancelled by sender</code>",
            parse_mode=enums.ParseMode.HTML
        )
        await callback_query.answer("Gift cancelled!", show_alert=False)
        
        logger.info(f"Gift cancelled: {sender_id} -> {receiver_id}")


# Command to check pending trades/gifts
@shivuu.on_message(filters.command("pending"))
async def check_pending(client, message):
    """Check user's pending trades and gifts"""
    user_id = message.from_user.id
    
    await cleanup_expired_operations()
    
    # Find user's pending operations
    user_trades = []
    user_gifts = []
    
    for (sender_id, receiver_id), data in pending_trades.items():
        if sender_id == user_id:
            user_trades.append("<code>├ Trade as sender (awaiting)</code>")
        elif receiver_id == user_id:
            user_trades.append("<code>├ Trade as receiver (action needed)</code>")
    
    for (sender_id, receiver_id), data in pending_gifts.items():
        if sender_id == user_id:
            user_gifts.append("<code>├ Gift (awaiting confirmation)</code>")
    
    if not user_trades and not user_gifts:
        await message.reply_text(
            "╭━━━━━━━━━━━━━━━╮\n"
            "┃  <b>✅ ALL CLEAR</b>    ┃\n"
            "╰━━━━━━━━━━━━━━━╯\n\n"
            "<code>📋 No pending operations!</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    msg = (
        "╭━━━━━━━━━━━━━━━━━╮\n"
        "┃  <b>📋 PENDING OPS</b>     ┃\n"
        "╰━━━━━━━━━━━━━━━━━╯\n\n"
    )
    
    if user_trades:
        msg += "<b>🔄 Trades:</b>\n" + "\n".join(user_trades) + "\n<code>└─────────────────</code>\n\n"
    
    if user_gifts:
        msg += "<b>🎁 Gifts:</b>\n" + "\n".join(user_gifts) + "\n<code>└─────────────────</code>"
    
    await message.reply_text(msg, parse_mode=enums.ParseMode.HTML)


# Admin command to clear all pending operations
@shivuu.on_message(filters.command("clearpending") & filters.user("ADMIN_USER_ID"))  # Replace with actual admin ID
async def clear_pending(client, message):
    """Clear all pending trades and gifts (Admin only)"""
    pending_trades.clear()
    pending_gifts.clear()
    await message.reply_text(
        "╭━━━━━━━━━━━━━━━━╮\n"
        "┃  <b>✅ CLEARED</b>       ┃\n"
        "╰━━━━━━━━━━━━━━━━╯\n\n"
        "<code>🗑️ All pending operations cleared!</code>",
        parse_mode=enums.ParseMode.HTML
    )
