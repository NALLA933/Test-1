import os
import html
import tempfile
import datetime
from typing import Optional, Dict, Tuple
from functools import lru_cache

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters
)
import cachetools
from bson import ObjectId

from shivu import (
    application,
    user_collection,
    top_global_groups_collection,
    group_user_totals_collection
)
from shivu.config import Config

# ============================
# Database Collections
# ============================
from shivu import db
premium_users_collection = db.premium_users
leaderboard_cache_collection = db.leaderboard_cache

# ============================
# Premium Constants
# ============================
PREMIUM_PRICE_USD = 4.99
PREMIUM_DURATION_DAYS = 30
FREE_LIMIT = 10
PREMIUM_LIMIT = 25

# ============================
# Caching
# ============================
leaderboard_cache = cachetools.TTLCache(maxsize=50, ttl=60)
user_rank_cache = cachetools.TTLCache(maxsize=1000, ttl=30)

# ============================
# Helper Functions
# ============================
def get_rank_icon(rank: int) -> str:
    """Get icon for rank position."""
    if rank == 1:
        return "🥇"
    elif rank == 2:
        return "🥈"
    elif rank == 3:
        return "🥉"
    else:
        return f"{rank}."

async def is_premium_user(user_id: int) -> bool:
    """Check if user has active premium subscription."""
    premium_user = await premium_users_collection.find_one({
        "user_id": user_id,
        "expires_at": {"$gt": datetime.datetime.utcnow()}
    })
    return premium_user is not None

async def get_leaderboard_limit(user_id: int) -> int:
    """Get leaderboard limit based on premium status."""
    return PREMIUM_LIMIT if await is_premium_user(user_id) else FREE_LIMIT

def truncate_name(name: str, max_length: int = 18) -> str:
    """Truncate name safely for display."""
    if len(name) <= max_length:
        return html.escape(name)
    return html.escape(name[:max_length]) + "..."

def get_leaderboard_keyboard(leaderboard_type: str, chat_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Create interactive keyboard for leaderboards."""
    buttons = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{leaderboard_type}"),
            InlineKeyboardButton("👤 My Rank", callback_data=f"myrank_{leaderboard_type}")
        ]
    ]
    
    if leaderboard_type in ["global", "group"]:
        buttons.append([
            InlineKeyboardButton("⭐ Upgrade Premium", callback_data="upgrade_premium")
        ])
    
    return InlineKeyboardMarkup(buttons)

# ============================
# Leaderboard Services
# ============================
async def get_global_user_leaderboard(limit: int = 10) -> list:
    """Get global user leaderboard with optimized aggregation."""
    cache_key = f"global_users_{limit}"
    if cache_key in leaderboard_cache:
        return leaderboard_cache[cache_key]
    
    pipeline = [
        {
            "$addFields": {
                "character_count": {"$size": "$characters"},
                "join_date": {"$ifNull": ["$started_at", None]}
            }
        },
        {"$sort": {"character_count": -1}},
        {"$limit": limit},
        {
            "$project": {
                "user_id": "$_id",
                "username": 1,
                "first_name": 1,
                "character_count": 1,
                "join_date": 1
            }
        }
    ]
    
    cursor = user_collection.aggregate(pipeline)
    result = await cursor.to_list(length=limit)
    leaderboard_cache[cache_key] = result
    return result

async def get_group_leaderboard(chat_id: int, limit: int = 10) -> list:
    """Get group leaderboard with optimized aggregation."""
    cache_key = f"group_{chat_id}_{limit}"
    if cache_key in leaderboard_cache:
        return leaderboard_cache[cache_key]
    
    pipeline = [
        {"$match": {"group_id": chat_id}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
        {
            "$project": {
                "user_id": "$user_id",
                "username": 1,
                "first_name": 1,
                "character_count": "$count",
                "group_id": 1
            }
        }
    ]
    
    cursor = group_user_totals_collection.aggregate(pipeline)
    result = await cursor.to_list(length=limit)
    leaderboard_cache[cache_key] = result
    return result

async def get_top_groups_leaderboard(limit: int = 10) -> list:
    """Get top groups leaderboard."""
    cache_key = f"top_groups_{limit}"
    if cache_key in leaderboard_cache:
        return leaderboard_cache[cache_key]
    
    pipeline = [
        {"$sort": {"count": -1}},
        {"$limit": limit},
        {
            "$project": {
                "group_id": 1,
                "group_name": 1,
                "count": 1
            }
        }
    ]
    
    cursor = top_global_groups_collection.aggregate(pipeline)
    result = await cursor.to_list(length=limit)
    leaderboard_cache[cache_key] = result
    return result

async def get_user_global_rank(user_id: int) -> Tuple[int, int]:
    """Get user's global rank and total characters efficiently."""
    cache_key = f"user_rank_{user_id}"
    if cache_key in user_rank_cache:
        return user_rank_cache[cache_key]
    
    # Get user's character count
    user = await user_collection.find_one({"_id": user_id})
    if not user:
        user_rank_cache[cache_key] = (0, 0)
        return (0, 0)
    
    character_count = len(user.get("characters", []))
    
    # Count users with more characters (rank calculation)
    rank = await user_collection.count_documents({
        "$or": [
            {"characters": {"$size": {"$gt": character_count}}},
            {
                "$and": [
                    {"characters": {"$size": character_count}},
                    {"_id": {"$lt": user_id}}
                ]
            }
        ]
    }) + 1
    
    result = (rank, character_count)
    user_rank_cache[cache_key] = result
    return result

async def get_user_group_rank(user_id: int, chat_id: int) -> Tuple[int, int]:
    """Get user's rank within a specific group."""
    cache_key = f"user_group_rank_{user_id}_{chat_id}"
    if cache_key in user_rank_cache:
        return user_rank_cache[cache_key]
    
    # Get user's count in this group
    group_user = await group_user_totals_collection.find_one({
        "user_id": user_id,
        "group_id": chat_id
    })
    
    if not group_user:
        user_rank_cache[cache_key] = (0, 0)
        return (0, 0)
    
    user_count = group_user.get("count", 0)
    
    # Count users in group with higher count
    rank = await group_user_totals_collection.count_documents({
        "group_id": chat_id,
        "$or": [
            {"count": {"$gt": user_count}},
            {"count": user_count, "user_id": {"$lt": user_id}}
        ]
    }) + 1
    
    result = (rank, user_count)
    user_rank_cache[cache_key] = result
    return result

# ============================
# Premium Services
# ============================
async def activate_premium(user_id: int, duration_days: int = PREMIUM_DURATION_DAYS):
    """Activate premium subscription for user."""
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=duration_days)
    
    await premium_users_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "expires_at": expires_at,
                "activated_at": datetime.datetime.utcnow(),
                "duration_days": duration_days
            }
        },
        upsert=True
    )
    
    # Clear user's cache
    for key in list(user_rank_cache.keys()):
        if str(user_id) in key:
            user_rank_cache.pop(key, None)

# ============================
# Payment Handlers
# ============================
async def upgrade_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send invoice for premium upgrade."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Check if already premium
    if await is_premium_user(user_id):
        await update.message.reply_text(
            "⭐ You already have an active premium subscription!"
        )
        return
    
    # Provider token should be set in Config
    provider_token = getattr(Config, "PAYMENT_PROVIDER_TOKEN", None)
    if not provider_token:
        await update.message.reply_text(
            "⚠️ Payment system is temporarily unavailable. Please try again later."
        )
        return
    
    title = "Premium Subscription"
    description = (
        "✨ Unlock Premium Features:\n"
        "• Top 25 leaderboard access\n"
        "• Weekly statistics\n"
        "• Priority support\n"
        f"• Valid for {PREMIUM_DURATION_DAYS} days"
    )
    
    payload = f"premium_{user_id}"
    currency = "USD"
    prices = [LabeledPrice("Premium Subscription", int(PREMIUM_PRICE_USD * 100))]
    
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=prices,
        start_parameter="premium_subscription"
    )

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pre-checkout query."""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful payment and activate premium."""
    user_id = update.effective_user.id
    await activate_premium(user_id)
    
    await update.message.reply_text(
        "🎉 Thank you for upgrading to Premium!\n\n"
        "Your subscription is now active for 30 days. "
        "You now have access to all premium features!"
    )

# ============================
# Leaderboard Handlers (Enhanced)
# ============================
async def global_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display global user leaderboard."""
    user_id = update.effective_user.id
    limit = await get_leaderboard_limit(user_id)
    
    leaderboard_data = await get_global_user_leaderboard(limit)
    
    # Build premium-style message
    message = "🥇 <b>Global Leaderboard</b> 🥇\n\n"
    
    for i, user in enumerate(leaderboard_data, start=1):
        first_name = truncate_name(user.get("first_name", "Unknown"))
        username = user.get("username", "")
        count = user.get("character_count", 0)
        
        if username:
            entry = f"{get_rank_icon(i)} <a href='https://t.me/{username}'>{first_name}</a> ➾ {count}"
        else:
            entry = f"{get_rank_icon(i)} {first_name} ➾ {count}"
        
        # Add star for premium users in top 10
        if i <= 10 and await is_premium_user(user.get("user_id")):
            entry += " ⭐"
        
        message += entry + "\n"
    
    # Add footer
    premium_status = "⭐ Premium" if limit == PREMIUM_LIMIT else "👤 Free"
    message += f"\n└─ <i>Showing top {limit} • {premium_status}</i>"
    message += "\n\n✨ <i>Powered by Premium Engine</i>"
    
    # Send with interactive keyboard
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=get_leaderboard_keyboard("global")
    )

async def ctop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display group leaderboard."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    limit = await get_leaderboard_limit(user_id)
    
    leaderboard_data = await get_group_leaderboard(chat_id, limit)
    
    message = "🏆 <b>Group Leaderboard</b> 🏆\n\n"
    
    for i, user in enumerate(leaderboard_data, start=1):
        first_name = truncate_name(user.get("first_name", "Unknown"))
        username = user.get("username", "")
        count = user.get("character_count", 0)
        
        if username:
            entry = f"{get_rank_icon(i)} <a href='https://t.me/{username}'>{first_name}</a> ➾ {count}"
        else:
            entry = f"{get_rank_icon(i)} {first_name} ➾ {count}"
        
        # Add flame for top 3
        if i <= 3:
            entry = f"🔥 {entry}"
        
        message += entry + "\n"
    
    # Add footer
    premium_status = "⭐ Premium" if limit == PREMIUM_LIMIT else "👤 Free"
    message += f"\n└─ <i>Showing top {limit} • {premium_status}</i>"
    message += "\n\n✨ <i>Powered by Premium Engine</i>"
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=get_leaderboard_keyboard("group", chat_id)
    )

async def topgroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display top groups leaderboard."""
    limit = 10  # Groups leaderboard is same for all
    
    leaderboard_data = await get_top_groups_leaderboard(limit)
    
    message = "👥 <b>Top Groups</b> 👥\n\n"
    
    for i, group in enumerate(leaderboard_data, start=1):
        group_name = truncate_name(group.get("group_name", "Unknown Group"))
        count = group.get("count", 0)
        
        message += f"{get_rank_icon(i)} <b>{group_name}</b> ➾ {count}\n"
    
    message += f"\n└─ <i>Showing top {limit} groups</i>"
    message += "\n\n✨ <i>Powered by Premium Engine</i>"
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=get_leaderboard_keyboard("groups")
    )

# ============================
# New Handlers
# ============================
async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user's personal statistics."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Get global rank
    global_rank, global_count = await get_user_global_rank(user_id)
    
    # Get group rank
    group_rank, group_count = await get_user_group_rank(user_id, chat_id)
    
    # Check premium status
    premium = await is_premium_user(user_id)
    
    # Build stats message
    message = "📊 <b>Your Statistics</b> 📊\n\n"
    message += f"🏆 <b>Global Rank:</b> #{global_rank}\n"
    message += f"📈 <b>Characters Collected:</b> {global_count}\n\n"
    
    if group_rank > 0:
        message += f"👥 <b>Group Rank:</b> #{group_rank}\n"
        message += f"🎯 <b>Group Guesses:</b> {group_count}\n\n"
    
    message += f"⭐ <b>Premium Status:</b> {'Active' if premium else 'Inactive'}\n"
    
    if premium:
        # Get premium info
        premium_data = await premium_users_collection.find_one({"user_id": user_id})
        if premium_data:
            expires_at = premium_data.get("expires_at")
            if expires_at:
                days_left = (expires_at - datetime.datetime.utcnow()).days
                message += f"⏳ <b>Days Remaining:</b> {days_left}\n"
    
    message += "\n✨ <i>Powered by Premium Engine</i>"
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📈 View Leaderboards", callback_data="view_leaderboards"),
        InlineKeyboardButton("⭐ Upgrade", callback_data="upgrade_premium")
    ]])
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=keyboard
    )

# ============================
# Callback Query Handler
# ============================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("refresh_"):
        # Clear cache for this leaderboard type
        leaderboard_type = data.split("_")[1]
        for key in list(leaderboard_cache.keys()):
            if leaderboard_type in key:
                leaderboard_cache.pop(key, None)
        
        # Edit message to show refreshing
        await query.edit_message_text(
            "🔄 Refreshing...",
            parse_mode="HTML"
        )
        
        # Determine which leaderboard to refresh
        if leaderboard_type == "global":
            limit = await get_leaderboard_limit(user_id)
            leaderboard_data = await get_global_user_leaderboard(limit)
            
            message = "🥇 <b>Global Leaderboard</b> 🥇\n\n"
            for i, user in enumerate(leaderboard_data, start=1):
                first_name = truncate_name(user.get("first_name", "Unknown"))
                username = user.get("username", "")
                count = user.get("character_count", 0)
                
                if username:
                    entry = f"{get_rank_icon(i)} <a href='https://t.me/{username}'>{first_name}</a> ➾ {count}"
                else:
                    entry = f"{get_rank_icon(i)} {first_name} ➾ {count}"
                
                message += entry + "\n"
            
            premium_status = "⭐ Premium" if limit == PREMIUM_LIMIT else "👤 Free"
            message += f"\n└─ <i>Showing top {limit} • {premium_status}</i>"
        
        elif leaderboard_type == "group":
            chat_id = query.message.chat_id
            limit = await get_leaderboard_limit(user_id)
            leaderboard_data = await get_group_leaderboard(chat_id, limit)
            
            message = "🏆 <b>Group Leaderboard</b> 🏆\n\n"
            for i, user in enumerate(leaderboard_data, start=1):
                first_name = truncate_name(user.get("first_name", "Unknown"))
                username = user.get("username", "")
                count = user.get("character_count", 0)
                
                if username:
                    entry = f"{get_rank_icon(i)} <a href='https://t.me/{username}'>{first_name}</a> ➾ {count}"
                else:
                    entry = f"{get_rank_icon(i)} {first_name} ➾ {count}"
                
                message += entry + "\n"
            
            premium_status = "⭐ Premium" if limit == PREMIUM_LIMIT else "👤 Free"
            message += f"\n└─ <i>Showing top {limit} • {premium_status}</i>"
        
        message += "\n\n✨ <i>Powered by Premium Engine</i>"
        
        await query.edit_message_text(
            message,
            parse_mode="HTML",
            reply_markup=get_leaderboard_keyboard(leaderboard_type, query.message.chat_id)
        )
    
    elif data.startswith("myrank_"):
        leaderboard_type = data.split("_")[1]
        chat_id = query.message.chat_id
        
        if leaderboard_type == "global":
            rank, count = await get_user_global_rank(user_id)
            message = f"🏆 <b>Your Global Rank:</b> #{rank}\n"
            message += f"📊 <b>Characters:</b> {count}"
        
        elif leaderboard_type == "group":
            rank, count = await get_user_group_rank(user_id, chat_id)
            if rank > 0:
                message = f"👥 <b>Your Group Rank:</b> #{rank}\n"
                message += f"🎯 <b>Guesses:</b> {count}"
            else:
                message = "📭 You haven't guessed any characters in this group yet."
        
        await query.answer(message, show_alert=True)
    
    elif data == "upgrade_premium":
        await query.message.reply_text(
            "⭐ <b>Premium Upgrade</b>\n\n"
            "Unlock these premium features:\n"
            "• Top 25 leaderboard access\n"
            "• Advanced statistics\n"
            "• Priority support\n"
            "• No ads\n\n"
            f"Price: ${PREMIUM_PRICE_USD} for {PREMIUM_DURATION_DAYS} days\n\n"
            "Click below to upgrade:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Upgrade Now", callback_data="initiate_upgrade")
            ]])
        )
    
    elif data == "initiate_upgrade":
        await upgrade_premium(update, context)
    
    elif data == "view_leaderboards":
        await query.message.reply_text(
            "📊 <b>Available Leaderboards</b>\n\n"
            "/top - Global user ranking\n"
            "/ctop - Current group ranking\n"
            "/topgroups - Top groups ranking\n"
            "/mystats - Your personal stats",
            parse_mode="HTML"
        )

# ============================
# Existing Handlers (Updated)
# ============================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only statistics."""
    user_id = update.effective_user.id
    
    if user_id != Config.OWNER_ID:
        await update.message.reply_text("🔒 Owner only.")
        return
    
    user_count = await user_collection.count_documents({})
    group_count = await group_user_totals_collection.distinct('group_id')
    premium_count = await premium_users_collection.count_documents({
        "expires_at": {"$gt": datetime.datetime.utcnow()}
    })
    
    stats_text = (
        "📊 <b>System Statistics</b>\n\n"
        f"👥 Total Users: {user_count}\n"
        f"👥 Active Groups: {len(group_count)}\n"
        f"⭐ Premium Users: {premium_count}\n"
        f"💾 Cache Size: {len(leaderboard_cache)} items"
    )
    
    await update.message.reply_text(stats_text, parse_mode="HTML")

async def send_users_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sudo-only user list export."""
    user_id = update.effective_user.id
    
    if user_id not in Config.SUDO_USERS:
        await update.message.reply_text("🔒 Sudo only.")
        return
    
    cursor = user_collection.find({})
    users = []
    
    async for document in cursor:
        first_name = document.get('first_name', 'Unknown')
        user_id = document.get('_id', '')
        users.append(f"{first_name} (ID: {user_id})")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write('\n'.join(users))
        temp_path = f.name
    
    try:
        with open(temp_path, 'rb') as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename='users_export.txt',
                caption="📁 User List Export"
            )
    finally:
        os.unlink(temp_path)

async def send_groups_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sudo-only group list export."""
    user_id = update.effective_user.id
    
    if user_id not in Config.SUDO_USERS:
        await update.message.reply_text("🔒 Sudo only.")
        return
    
    cursor = top_global_groups_collection.find({})
    groups = []
    
    async for document in cursor:
        group_name = document.get('group_name', 'Unknown')
        group_id = document.get('group_id', '')
        count = document.get('count', 0)
        groups.append(f"{group_name} (ID: {group_id}) - {count} guesses")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write('\n'.join(groups))
        temp_path = f.name
    
    try:
        with open(temp_path, 'rb') as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename='groups_export.txt',
                caption="📁 Group List Export"
            )
    finally:
        os.unlink(temp_path)

# ============================
# Register Handlers
# ============================
# Command Handlers
application.add_handler(CommandHandler('top', global_leaderboard))
application.add_handler(CommandHandler('ctop', ctop))
application.add_handler(CommandHandler('topgroups', topgroups))
application.add_handler(CommandHandler('stats', stats))
application.add_handler(CommandHandler('list', send_users_document))
application.add_handler(CommandHandler('groups', send_groups_document))
application.add_handler(CommandHandler('mystats', mystats))
application.add_handler(CommandHandler('upgrade', upgrade_premium))

# Callback Query Handler
application.add_handler(CallbackQueryHandler(button_callback))

# Payment Handlers
application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))