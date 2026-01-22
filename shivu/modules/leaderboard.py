import os
import random
import html
import tempfile
from typing import List

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
import cachetools

from shivu import (
    application,
    user_collection,
    top_global_groups_collection,
    group_user_totals_collection
)
from config import Config


video_cache = cachetools.LRUCache(maxsize=100)


async def global_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = [
        {"$sort": {"count": -1}},
        {"$limit": 10},
        {"$project": {"group_name": 1, "count": 1}}
    ]
    
    cursor = top_global_groups_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)

    leaderboard_message = "❖ Top 10 Groups ❖\n\n"

    for i, group in enumerate(leaderboard_data, start=1):
        group_name = html.escape(group.get('group_name', 'Unknown'))
        if len(group_name) > 15:
            group_name = group_name[:15] + '...'
        count = group['count']
        leaderboard_message += f'{i}⟡ <b>{group_name}</b> ➾ <b>{count}</b>\n'

    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML'
    )


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

    leaderboard_message = "❖ Top 10 Group Users ❖\n\n"

    for i, user in enumerate(leaderboard_data, start=1):
        username = user.get('username', '')
        first_name = html.escape(user.get('first_name', 'Unknown'))
        
        if len(first_name) > 15:
            first_name = first_name[:15] + '...'
        
        character_count = user['character_count']
        
        if username:
            leaderboard_message += f'{i}⟡ <a href="https://t.me/{username}"><b>{first_name}</b></a> ➾ <b>{character_count}</b>\n'
        else:
            leaderboard_message += f'{i}⟡ <b>{first_name}</b> ➾ <b>{character_count}</b>\n'

    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML'
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pipeline = [
        {"$addFields": {"character_count": {"$size": "$characters"}}},
        {"$sort": {"character_count": -1}},
        {"$limit": 10},
        {"$project": {"username": 1, "first_name": 1, "character_count": 1}}
    ]
    
    cursor = user_collection.aggregate(pipeline)
    leaderboard_data = await cursor.to_list(length=10)

    leaderboard_message = "❖ Top 10 Global Users ❖\n\n"

    for i, user in enumerate(leaderboard_data, start=1):
        username = user.get('username', '')
        first_name = html.escape(user.get('first_name', 'Unknown'))
        
        if len(first_name) > 15:
            first_name = first_name[:15] + '...'
        
        character_count = user['character_count']
        
        if username:
            leaderboard_message += f'{i}⟡ <a href="https://t.me/{username}"><b>{first_name}</b></a> ➾ <b>{character_count}</b>\n'
        else:
            leaderboard_message += f'{i}⟡ <b>{first_name}</b> ➾ <b>{character_count}</b>\n'

    video_url = random.choice(Config.VIDEO_URL)
    await update.message.reply_video(
        video=video_url,
        caption=leaderboard_message,
        parse_mode='HTML'
    )


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


application.add_handler(CommandHandler('ctop', ctop))
application.add_handler(CommandHandler('stats', stats))
application.add_handler(CommandHandler('topgroups', global_leaderboard))
application.add_handler(CommandHandler('list', send_users_document))
application.add_handler(CommandHandler('groups', send_groups_document))
application.add_handler(CommandHandler('top', leaderboard))