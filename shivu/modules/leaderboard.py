import html
import random
from datetime import datetime, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler

from shivu import (
    application, VIDEO_URL, user_collection, top_global_groups_collection,
    group_user_totals_collection, LOGGER, collection
)


from cachetools import TTLCache

CACHE_TTL = 300
cache = TTLCache(maxsize=100, ttl=CACHE_TTL)


from shivu.utils import to_small_caps


def get_ist_date() -> str:
    ist_tz = pytz.timezone('Asia/Kolkata')
    ist_now = datetime.now(ist_tz)
    return ist_now.strftime("%Y-%m-%d")


def get_ist_datetime() -> datetime:
    ist_tz = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist_tz)


_daily_db = user_collection.database
daily_user_guesses_collection = _daily_db.get_collection('daily_user_guesses')
daily_group_guesses_collection = _daily_db.get_collection('daily_group_guesses')


_indexes_initialized = False


async def setup_database_indexes():
    try:
        LOGGER.info("Setting up database indexes...")
        
        await user_collection.create_index([("balance", -1)], background=True)
        await user_collection.create_index([("characters", 1)], background=True)
        
        await daily_user_guesses_collection.create_index(
            [("date", 1), ("count", -1)], 
            background=True
        )
        await daily_user_guesses_collection.create_index(
            [("date", 1), ("user_id", 1)],
            unique=True,
            background=True
        )
        
        await daily_group_guesses_collection.create_index(
            [("date", 1), ("count", -1)],
            background=True
        )
        await daily_group_guesses_collection.create_index(
            [("date", 1), ("group_id", 1)],
            unique=True,
            background=True
        )
        
        LOGGER.info("Database indexes created successfully!")
    except Exception as e:
        LOGGER.error(f"Error creating indexes: {e}")


async def initialize_leaderboard():
    global _indexes_initialized
    if not _indexes_initialized:
        await setup_database_indexes()
        _indexes_initialized = True
        LOGGER.info("Leaderboard indexes initialized!")


async def update_daily_user_guess(user_id: int, username: str = "", first_name: str = "") -> None:
    try:
        today = get_ist_date()

        safe_username = username if username else ""
        safe_first_name = first_name if first_name else "Unknown"

        await daily_user_guesses_collection.update_one(
            {
                "date": today,
                "user_id": user_id
            },
            {
                "$inc": {"count": 1},
                "$set": {
                    "username": safe_username,
                    "first_name": safe_first_name,
                    "last_updated": get_ist_datetime()
                },
                "$setOnInsert": {
                    "date": today,
                    "user_id": user_id
                }
            },
            upsert=True
        )
        
        keys_to_delete = [k for k in cache.keys() if "leaderboard:user:" in k]
        for k in keys_to_delete:
            cache.pop(k, None)
        
        LOGGER.info(f"Daily user guess updated: user_id={user_id}, date={today}")
    except Exception as e:
        LOGGER.error(f"Error updating daily user guess for user_id {user_id}: {e}")


async def update_daily_group_guess(group_id: int, group_name: str = "") -> None:
    try:
        today = get_ist_date()

        safe_group_name = group_name if group_name else "Unknown Group"

        await daily_group_guesses_collection.update_one(
            {
                "date": today,
                "group_id": group_id
            },
            {
                "$inc": {"count": 1},
                "$set": {
                    "group_name": safe_group_name,
                    "last_updated": get_ist_datetime()
                },
                "$setOnInsert": {
                    "date": today,
                    "group_id": group_id
                }
            },
            upsert=True
        )
        
        keys_to_delete = [k for k in cache.keys() if "leaderboard:group:" in k]
        for k in keys_to_delete:
            cache.pop(k, None)
        
        LOGGER.info(f"Daily group guess updated: group_id={group_id}, date={today}")
    except Exception as e:
        LOGGER.error(f"Error updating daily group guess for group_id {group_id}: {e}")


async def show_char_top() -> str:
    try:
        await initialize_leaderboard()
        
        cache_key = "leaderboard:char:top10"
        cached = cache.get(cache_key)
        if cached:
            LOGGER.info("Serving character leaderboard from cache")
            return cached
        
        LOGGER.info("Generating fresh character leaderboard...")
        
        pipeline = [
            {
                "$project": {
                    "username": 1,
                    "first_name": 1,
                    "character_count": {
                        "$cond": {
                            "if": {"$isArray": "$characters"},
                            "then": {"$size": "$characters"},
                            "else": 0
                        }
                    }
                }
            },
            {"$match": {"character_count": {"$gt": 0}}},
            {"$sort": {"character_count": -1}},
            {"$limit": 10}
        ]
        
        cursor = user_collection.aggregate(pipeline, allowDiskUse=True)
        leaderboard_data = await cursor.to_list(length=10)

        message = "🏆 <b>ᴛᴏᴘ 10 ᴜsᴇʀs ᴡɪᴛʜ ᴍᴏsᴛ ᴄʜᴀʀᴀᴄᴛᴇʀs</b>\n\n"

        if not leaderboard_data:
            message += "ɴᴏ ᴅᴀᴛᴀ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ!"
            cache[cache_key] = message
            return message

        for i, user in enumerate(leaderboard_data, start=1):
            username = user.get('username', '')
            first_name = html.escape(user.get('first_name', 'Unknown'))

            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            character_count = user.get('character_count', 0)

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{character_count}</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{character_count}</b>\n'

        cache[cache_key] = message
        LOGGER.info("Character leaderboard generated and cached")
        
        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_char_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_coin_top() -> str:
    try:
        cache_key = "leaderboard:coin:top10"
        cached = cache.get(cache_key)
        if cached:
            LOGGER.info("Serving coin leaderboard from cache")
            return cached
        
        LOGGER.info("Generating fresh coin leaderboard...")
        
        cursor = user_collection.aggregate([
            {"$sort": {"balance": -1}},
            {"$limit": 10},
            {"$project": {
                "username": 1,
                "first_name": 1,
                "balance": 1
            }}
        ], allowDiskUse=True)
        
        coin_data = await cursor.to_list(length=10)

        message = "💰 <b>ᴛᴏᴘ 10 ʀɪᴄʜᴇsᴛ ᴜsᴇʀs</b>\n\n"

        if not coin_data:
            message += "ɴᴏ ᴅᴀᴛᴀ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ!"
            cache[cache_key] = message
            return message

        for i, user_data in enumerate(coin_data, start=1):
            balance = user_data.get('balance', 0)
            username = user_data.get('username', '')
            first_name = html.escape(user_data.get('first_name', 'Unknown'))
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{balance} coins</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{balance} coins</b>\n'

        cache[cache_key] = message
        LOGGER.info("Coin leaderboard generated and cached")
        
        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_coin_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_group_top() -> str:
    try:
        today = get_ist_date()
        
        cache_key = f"leaderboard:group:top10:{today}"
        cached = cache.get(cache_key)
        if cached:
            LOGGER.info("Serving group leaderboard from cache")
            return cached
        
        LOGGER.info("Generating fresh group leaderboard...")

        cursor = daily_group_guesses_collection.aggregate([
            {"$match": {"date": today}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
            {"$project": {
                "group_name": 1,
                "count": 1
            }}
        ], allowDiskUse=True)

        daily_data = await cursor.to_list(length=10)

        if not daily_data:
            message = f"👥 <b>ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\nɴᴏ ɢᴜᴇssᴇs ᴛᴏᴅᴀʏ ʏᴇᴛ!"
            cache[cache_key] = message
            return message

        message = f"👥 <b>ᴛᴏᴘ 10 ɢʀᴏᴜᴘs ʙʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\n"

        for i, group in enumerate(daily_data, start=1):
            group_name = html.escape(group.get('group_name', 'Unknown'))
            display_name = to_small_caps(group_name)

            if len(display_name) > 20:
                display_name = display_name[:20] + '...'

            count = group.get('count', 0)
            message += f'{i}. <b>{display_name}</b> ➾ <b>{count}</b>\n'

        cache[cache_key] = message
        LOGGER.info("Group leaderboard generated and cached")
        
        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_group_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def show_group_user_top(chat_id: int | None = None) -> str:
    try:
        today = get_ist_date()
        
        cache_key = f"leaderboard:user:top10:{today}"
        cached = cache.get(cache_key)
        if cached:
            LOGGER.info("Serving user leaderboard from cache")
            return cached
        
        LOGGER.info("Generating fresh user leaderboard...")

        cursor = daily_user_guesses_collection.aggregate([
            {"$match": {"date": today}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
            {"$project": {
                "username": 1,
                "first_name": 1,
                "count": 1
            }}
        ], allowDiskUse=True)

        daily_data = await cursor.to_list(length=10)

        if not daily_data:
            message = f"⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏʀʀᴇᴄᴛ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\nɴᴏ ɢᴜᴇssᴇs ᴛᴏᴅᴀʏ ʏᴇᴛ!"
            cache[cache_key] = message
            return message

        message = f"⏳ <b>ᴛᴏᴘ 10 ᴜsᴇʀs ʙʏ ᴄᴏʀʀᴇᴄᴛ ɢᴜᴇssᴇs (ᴛᴏᴅᴀʏ)</b>\n📅 <i>{today}</i>\n\n"

        for i, user in enumerate(daily_data, start=1):
            username = user.get('username', '')
            first_name = html.escape(user.get('first_name', 'Unknown'))
            display_name = to_small_caps(first_name)

            if len(display_name) > 15:
                display_name = display_name[:15] + '...'

            count = user.get('count', 0)

            if username:
                message += f'{i}. <a href="https://t.me/{username}"><b>{display_name}</b></a> ➾ <b>{count}</b>\n'
            else:
                message += f'{i}. <b>{display_name}</b> ➾ <b>{count}</b>\n'

        cache[cache_key] = message
        LOGGER.info("User leaderboard generated and cached")
        
        return message
    except Exception as e:
        LOGGER.exception(f"Error in show_group_user_top: {e}")
        return "❌ <b>ᴇʀʀᴏʀ ʟᴏᴀᴅɪɴɢ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</b>"


async def leaderboard_entry(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [
            InlineKeyboardButton("💠 ᴛᴏᴘ ᴄᴏʟʟᴇᴄᴛᴏʀs", callback_data="leaderboard_char"),
            InlineKeyboardButton("💸 ᴛᴏᴘ ʙᴀʟᴀɴᴄᴇ", callback_data="leaderboard_coin")
        ],
        [
            InlineKeyboardButton("⚡ ɢʀᴏᴜᴘ ᴛᴏᴘ", callback_data="leaderboard_group"),
            InlineKeyboardButton("🍃 ᴛᴏᴘ ᴜsᴇʀs", callback_data="leaderboard_group_user")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    video_url = random.choice(VIDEO_URL)
    caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"

    await update.message.reply_video(
        video=video_url,
        caption=caption,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def leaderboard_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id

    main_keyboard = [
        [
            InlineKeyboardButton("💠 ᴛᴏᴘ ᴄᴏʟʟᴇᴄᴛᴏʀs", callback_data="leaderboard_char"),
            InlineKeyboardButton("💸 ᴛᴏᴘ ʙᴀʟᴀɴᴄᴇ", callback_data="leaderboard_coin")
        ],
        [
            InlineKeyboardButton("⚡ ɢʀᴏᴜᴘ ᴛᴏᴘ", callback_data="leaderboard_group"),
            InlineKeyboardButton("🍃 ᴛᴏᴘ ᴜsᴇʀs", callback_data="leaderboard_group_user")
        ]
    ]

    back_keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="leaderboard_main")]]

    try:
        if data == "leaderboard_main":
            caption = "📊 <b>ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ ᴍᴇɴᴜ</b>\n\nᴄʜᴏᴏꜱᴇ ᴀ ʀᴀɴᴋɪɴɢ ᴛᴏ ᴠɪᴇᴡ:"
            reply_markup = InlineKeyboardMarkup(main_keyboard)
            await query.edit_message_caption(caption=caption, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_char":
            message = await show_char_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_coin":
            message = await show_coin_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_group":
            message = await show_group_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)

        elif data == "leaderboard_group_user":
            message = await show_group_user_top()
            reply_markup = InlineKeyboardMarkup(back_keyboard)
            await query.edit_message_caption(caption=message, parse_mode='HTML', reply_markup=reply_markup)
    except Exception as e:
        LOGGER.exception(f"Error in leaderboard_callback: {e}")
        await query.answer("❌ Error loading leaderboard", show_alert=True)


application.add_handler(CommandHandler('leaderboard', leaderboard_entry, block=False))
application.add_handler(CallbackQueryHandler(leaderboard_callback, pattern=r'^leaderboard_.*$', block=False))

LOGGER.info("Optimized Leaderboard module loaded successfully!")
