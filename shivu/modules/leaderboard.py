import os
import random
import html
import tempfile
import math
from typing import List, Tuple, Dict, Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
import cachetools

from shivu import (
    application,
    user_collection,
    top_global_groups_collection,
    group_user_totals_collection
)
from shivu.config import Config

video_cache = cachetools.LRUCache(maxsize=100)

# Helper function to get rank badge
def get_rank_badge(rank: int) -> str:
    """Returns the rank badge for a given position."""
    if rank == 1:
        return "★1ST★"
    elif rank == 2:
        return "★2ND★"
    elif rank == 3:
        return "★3RD★"
    else:
        return f"TOP {rank}"

# Helper function to get progress bar
def get_progress_bar(current_score: int, top_score: int, bar_length: int = 10) -> str:
    """Returns a visual progress bar based on current score relative to top score."""
    if top_score == 0:
        return "▱" * bar_length
    
    # Calculate fill percentage (top score = 100%)
    fill_ratio = current_score / top_score
    filled_length = int(round(bar_length * fill_ratio))
    
    # Ensure at least one filled segment if score > 0
    if current_score > 0 and filled_length == 0:
        filled_length = 1
    
    # Create progress bar
    filled = "▰" * filled_length
    empty = "▱" * (bar_length - filled_length)
    return filled + empty

# Helper function to format a leaderboard entry
def format_entry(rank: int, name: str, count: int, top_count: int, 
                 is_user: bool = False, username: str = None) -> str:
    """Formats a single leaderboard entry with rank badge and progress bar."""
    # Get rank badge
    badge = get_rank_badge(rank)
    
    # Create name with HTML formatting
    escaped_name = html.escape(name)
    if len(escaped_name) > 20:
        escaped_name = escaped_name[:20] + '...'
    
    # Create display name (with link for users if username exists)
    if is_user and username:
        display_name = f'<a href="https://t.me/{username}">{escaped_name}</a>'
    else:
        display_name = f'<b>{escaped_name}</b>'
    
    # Get progress bar
    progress_bar = get_progress_bar(count, top_count)
    
    # Format the entry
    entry = (
        f"{badge} {display_name}\n"
        f"  {progress_bar} <b>{count:,}</b>\n"
    )
    return entry

# Helper function to create leaderboard header
def get_leaderboard_header(title: str) -> str:
    """Returns a formatted header for the leaderboard."""
    return f"🏆 <b>{title}</b> 🏆\n\n"

# Refactored global_leaderboard function
async def global_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = [
        {"$sort": {"count": -1}},
        {"$limit": 10},
        {"$project": {"group_name": 1, "count": 1}}
    ]
    
    cursor = top_global_groups_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)
    
    if not leaderboard_data:
        leaderboard_message = "No group data available yet!"
    else:
        # Get top score for progress bar reference
        top_score = leaderboard_data[0]['count'] if leaderboard_data else 1
        
        # Create leaderboard message
        leaderboard_message = get_leaderboard_header("TOP 10 GROUPS")
        
        for i, group in enumerate(leaderboard_data, start=1):
            group_name = group.get('group_name', 'Unknown')
            count = group['count']
            
            # Format the entry
            leaderboard_message += format_entry(
                rank=i,
                name=group_name,
                count=count,
                top_count=top_score,
                is_user=False
            )
            leaderboard_message += "\n"  # Add spacing between entries
    
    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML',
        quote=True  # Crucial: Reply to user's command message
    )

# Refactored ctop function
async def ctop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    
    pipeline = [
        {"$match": {"group_id": chat_id}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
        {"$project": {"username": 1, "first_name": 1, "character_count": "$count"}}
    ]
    
    cursor = group_user_totals_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)
    
    if not leaderboard_data:
        leaderboard_message = "No user data available for this group yet!"
    else:
        # Get top score for progress bar reference
        top_score = leaderboard_data[0]['character_count'] if leaderboard_data else 1
        
        # Create leaderboard message
        leaderboard_message = get_leaderboard_header("TOP 10 GROUP USERS")
        
        for i, user in enumerate(leaderboard_data, start=1):
            username = user.get('username', '')
            first_name = user.get('first_name', 'Unknown')
            character_count = user['character_count']
            
            # Format the entry
            leaderboard_message += format_entry(
                rank=i,
                name=first_name,
                count=character_count,
                top_count=top_score,
                is_user=True,
                username=username if username else None
            )
            leaderboard_message += "\n"  # Add spacing between entries
    
    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML',
        quote=True  # Crucial: Reply to user's command message
    )

# Refactored leaderboard function (global users)
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = [
        {"$addFields": {"character_count": {"$size": "$characters"}}},
        {"$sort": {"character_count": -1}},
        {"$limit": 10},
        {"$project": {"username": 1, "first_name": 1, "character_count": 1}}
    ]
    
    cursor = user_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)
    
    if not leaderboard_data:
        leaderboard_message = "No global user data available yet!"
    else:
        # Get top score for progress bar reference
        top_score = leaderboard_data[0]['character_count'] if leaderboard_data else 1
        
        # Create leaderboard message
        leaderboard_message = get_leaderboard_header("TOP 10 GLOBAL USERS")
        
        for i, user in enumerate(leaderboard_data, start=1):
            username = user.get('username', '')
            first_name = user.get('first_name', 'Unknown')
            character_count = user['character_count']
            
            # Format the entry
            leaderboard_message += format_entry(
                rank=i,
                name=first_name,
                count=character_count,
                top_count=top_score,
                is_user=True,
                username=username if username else None
            )
            leaderboard_message += "\n"  # Add spacing between entries
    
    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML',
        quote=True  # Crucial: Reply to user's command message
    )

# Optional: Enhanced version with more gaming aesthetics (alternative)
async def leaderboard_enhanced(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced version with additional gaming aesthetics."""
    pipeline = [
        {"$addFields": {"character_count": {"$size": "$characters"}}},
        {"$sort": {"character_count": -1}},
        {"$limit": 10},
        {"$project": {"username": 1, "first_name": 1, "character_count": 1}}
    ]
    
    cursor = user_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)
    
    if not leaderboard_data:
        leaderboard_message = "🎮 <b>NO PLAYERS YET!</b> 🎮\nBe the first to collect characters!"
    else:
        # Get top score for progress bar reference
        top_score = leaderboard_data[0]['character_count'] if leaderboard_data else 1
        
        # Create enhanced header
        leaderboard_message = (
            "╔════════════════════════╗\n"
            "   🏆 <b>GLOBAL LEADERBOARD</b> 🏆\n"
            "╚════════════════════════╝\n\n"
        )
        
        for i, user in enumerate(leaderboard_data, start=1):
            username = user.get('username', '')
            first_name = user.get('first_name', 'Unknown')
            character_count = user['character_count']
            
            # Special badges for top 3
            if i == 1:
                badge = "🥇"
            elif i == 2:
                badge = "🥈"
            elif i == 3:
                badge = "🥉"
            else:
                badge = f"<b>{i}.</b>"
            
            # Progress bar with percentage
            progress_bar = get_progress_bar(character_count, top_score, 12)
            percentage = (character_count / top_score * 100) if top_score > 0 else 0
            
            # Format name
            escaped_name = html.escape(first_name)
            if len(escaped_name) > 15:
                escaped_name = escaped_name[:15] + '...'
            
            # Create display name
            if username:
                display_name = f'<a href="https://t.me/{username}">{escaped_name}</a>'
            else:
                display_name = f'<b>{escaped_name}</b>'
            
            # Enhanced entry format
            leaderboard_message += (
                f"{badge} {display_name}\n"
                f"   {progress_bar} <b>{character_count:,}</b> (<code>{percentage:.1f}%</code>)\n"
                f"╰──────────────────────────╯\n"
            )
    
    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML',
        quote=True
    )

# Keeping the stats and document functions unchanged
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if user_id != Config.OWNER_ID:
        await update.message.reply_text("Not authorized.")
        return

    user_count = await user_collection.count_documents({})
    group_count = await group_user_totals_collection.distinct('group_id')
    
    stats_text = f"✦ Stats ✦\n⌬ Users: {user_count}\n⌬ Groups: {len(group_count)}"
    
    await update.message.reply_text(stats_text)


async def send_users_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if user_id not in Config.SUDO_USERS:
        await update.message.reply_text("Sudo only.")
        return

    cursor = user_collection.find({})
    users: List[str] = []
    
    async for document in cursor:
        first_name = document.get('first_name', 'Unknown')
        users.append(first_name)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('\n'.join(users))
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename='users.txt'
            )
    finally:
        os.unlink(temp_path)


async def send_groups_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    if user_id not in Config.SUDO_USERS:
        await update.message.reply_text("Sudo only.")
        return

    cursor = top_global_groups_collection.find({})
    groups: List[str] = []
    
    async for document in cursor:
        group_name = document.get('group_name', 'Unknown')
        groups.append(group_name)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write('\n'.join(groups))
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename='groups.txt'
            )
    finally:
        os.unlink(temp_path)


# Register handlers
application.add_handler(CommandHandler('ctop', ctop))
application.add_handler(CommandHandler('stats', stats))
application.add_handler(CommandHandler('topgroups', global_leaderboard))
application.add_handler(CommandHandler('list', send_users_document))
application.add_handler(CommandHandler('groups', send_groups_document))
application.add_handler(CommandHandler('top', leaderboard))
# Optional: Add enhanced version as alternative
# application.add_handler(CommandHandler('topx', leaderboard_enhanced))