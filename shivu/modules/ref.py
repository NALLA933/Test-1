import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape
from shivu.utils import to_small_caps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

from shivu import application, db, BOT_USERNAME, GROUP_ID, OWNER_ID, SUDO_USERS
from shivu import user_collection

LOGGER = logging.getLogger(__name__)

REFERRAL_CONFIG_COLLECTION = db["referral_config"]
FRAUD_LOG_COLLECTION = db["fraud_log"]

DEFAULT_CONFIG = {
    "base_rewards": {
        "Bronze": 50,
        "Silver": 60,
        "Gold": 75,
        "Platinum": 100,
        "Diamond": 150,
    },
    "streak_multipliers": {
        1: 1.0,
        3: 1.2,
        5: 1.5,
        7: 2.0,
        14: 3.5,
        30: 6.0,
    },
    "tier_thresholds": {
        "Bronze": 0,
        "Silver": 10,
        "Gold": 50,
        "Platinum": 200,
        "Diamond": 1000,
    },
    "verification": {
        "min_messages": 3,
        "min_stay_seconds": 300,
        "max_pending_age_hours": 72,
        "min_account_age_days": 0,
    },
    "fraud": {
        "score_threshold": 10.0,
        "burst_window_seconds": 60,
        "burst_max_referrals": 3,
        "username_similarity_threshold": 0.8,
    },
}

_config_cache: dict | None = None
_config_cache_time: float = 0
CONFIG_TTL = 300


async def get_config() -> dict:
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache and (now - _config_cache_time) < CONFIG_TTL:
        return _config_cache
    doc = await REFERRAL_CONFIG_COLLECTION.find_one({"_id": "main"})
    if doc:
        doc.pop("_id", None)
        _config_cache = doc
    else:
        _config_cache = DEFAULT_CONFIG.copy()
    _config_cache_time = now
    return _config_cache


async def invalidate_config_cache():
    global _config_cache, _config_cache_time
    _config_cache = None
    _config_cache_time = 0


def generate_referral_code(user_id: int) -> str:
    raw = f"{user_id}-senpai-{int(time.time() // 86400)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


def get_utc_date_str(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def get_tier(verified_count: int, config: dict) -> str:
    thresholds = config["tier_thresholds"]
    tier = "Bronze"
    for name, minimum in sorted(thresholds.items(), key=lambda x: x[1]):
        if verified_count >= minimum:
            tier = name
    return tier


def get_base_reward(tier: str, config: dict) -> int:
    return config["base_rewards"].get(tier, 50)


def get_streak_multiplier(streak: int, config: dict) -> float:
    multipliers = config["streak_multipliers"]
    result = 1.0
    for days, mult in sorted(multipliers.items(), key=lambda x: x[0]):
        if streak >= int(days):
            result = mult
    return result


def calculate_reward(tier: str, streak: int, config: dict) -> tuple[int, float]:
    base = get_base_reward(tier, config)
    mult = get_streak_multiplier(streak, config)
    return int(base * mult), mult


async def get_balance(user_id: int) -> int:
    doc = await user_collection.find_one({"id": user_id}, {"balance": 1})
    return doc.get("balance", 0) if doc else 0


async def change_balance(user_id: int, amount: int) -> bool:
    if amount == 0:
        return True
    if amount < 0:
        result = await user_collection.find_one_and_update(
            {"id": user_id, "balance": {"$gte": abs(amount)}},
            {"$inc": {"balance": amount}},
            return_document=True,
        )
        return result is not None
    await user_collection.update_one(
        {"id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True,
    )
    return True


async def _atomic_transfer(sender_id: int, receiver_id: int, amount: int) -> bool:
    if amount <= 0:
        return False
    deducted = await change_balance(sender_id, -amount)
    if not deducted:
        return False
    await change_balance(receiver_id, amount)
    return True


async def ensure_referral_schema(user_id: int, username: str = None, first_name: str = None):
    code = generate_referral_code(user_id)
    await user_collection.update_one(
        {"id": user_id},
        {
            "$setOnInsert": {
                "referral": {
                    "code": code,
                    "referred_by": None,
                    "referrals": [],
                    "referral_count": 0,
                    "verified_referrals": 0,
                    "pending_referrals": [],
                    "streak": 0,
                    "longest_streak": 0,
                    "last_referral_date": None,
                    "total_earned": 0,
                    "milestone_level": "starter",
                    "tier": "Bronze",
                    "fraud_score": 0.0,
                    "referral_history": [],
                    "join_timestamp": time.time(),
                    "message_count": 0,
                    "first_seen": time.time(),
                    "is_verified": False,
                    "is_banned": False,
                }
            }
        },
        upsert=True,
    )


async def get_user_referral_data(user_id: int) -> dict | None:
    doc = await user_collection.find_one(
        {"id": user_id},
        {"referral": 1, "balance": 1, "id": 1},
    )
    return doc


async def get_referrer_by_code(code: str) -> dict | None:
    doc = await user_collection.find_one(
        {"referral.code": code},
        {"id": 1, "referral": 1},
    )
    return doc


async def increment_message_count(user_id: int):
    await user_collection.update_one(
        {"id": user_id},
        {"$inc": {"referral.message_count": 1}},
    )


async def compute_fraud_score(new_user_id: int, referrer_id: int, config: dict) -> float:
    score = 0.0
    cfg = config["fraud"]

    referrer_doc = await user_collection.find_one(
        {"id": referrer_id},
        {"referral.pending_referrals": 1, "referral.referral_history": 1},
    )

    if referrer_doc:
        pending = referrer_doc.get("referral", {}).get("pending_referrals", [])
        history = referrer_doc.get("referral", {}).get("referral_history", [])
        burst_window = cfg["burst_window_seconds"]
        now = time.time()
        recent = [h for h in history if now - h.get("timestamp", 0) <= burst_window]
        if len(recent) >= cfg["burst_max_referrals"]:
            score += 5.0

    new_doc = await user_collection.find_one(
        {"id": new_user_id},
        {"referral.first_seen": 1, "username": 1},
    )
    if new_doc:
        first_seen = new_doc.get("referral", {}).get("first_seen", time.time())
        age_days = (time.time() - first_seen) / 86400
        if age_days < 1:
            score += 3.0
        elif age_days < 7:
            score += 1.0

    return score


async def flag_fraud_user(user_id: int, reason: str, score: float):
    await user_collection.update_one(
        {"id": user_id},
        {"$set": {"referral.fraud_flagged": True, "referral.fraud_score": score}},
    )
    await FRAUD_LOG_COLLECTION.insert_one(
        {
            "user_id": user_id,
            "reason": reason,
            "score": score,
            "timestamp": time.time(),
        }
    )
    try:
        from telegram import Bot
        from shivu import TOKEN
        bot = Bot(token=TOKEN)
        await bot.send_message(
            chat_id=GROUP_ID,
            text=f"#ғʀᴀᴜᴅᴀʟᴇʀᴛ\n\nᴜsᴇʀ ɪᴅ: <code>{user_id}</code>\nʀᴇᴀsᴏɴ: {escape(reason)}\nsᴄᴏʀᴇ: {score:.2f}",
            parse_mode="HTML",
        )
    except Exception as e:
        LOGGER.error(f"Fraud notification failed: {e}")



async def process_referral_start(new_user_id: int, referral_code: str, context: ContextTypes.DEFAULT_TYPE):
    config = await get_config()

    referrer_doc = await get_referrer_by_code(referral_code)
    if not referrer_doc:
        return

    referrer_id = referrer_doc["id"]

    if referrer_id == new_user_id:
        return

    new_user_doc = await get_user_referral_data(new_user_id)
    if not new_user_doc:
        return

    ref_data = new_user_doc.get("referral", {})

    if ref_data.get("referred_by") is not None:
        return

    all_known = (
        referrer_doc.get("referral", {}).get("referrals", [])
        + referrer_doc.get("referral", {}).get("pending_referrals", [])
    )
    if new_user_id in all_known:
        return

    fraud_score = await compute_fraud_score(new_user_id, referrer_id, config)
    threshold = config["fraud"]["score_threshold"]

    if fraud_score >= threshold:
        await flag_fraud_user(new_user_id, "High fraud score at referral start", fraud_score)
        return

    await user_collection.update_one(
        {"id": new_user_id},
        {
            "$set": {
                "referral.referred_by": referrer_id,
                "referral.fraud_score": fraud_score,
            }
        },
    )

    await user_collection.update_one(
        {"id": referrer_id},
        {"$addToSet": {"referral.pending_referrals": new_user_id}},
    )

    LOGGER.info(f"Referral pending: {new_user_id} referred by {referrer_id}")


async def verify_and_reward_referral(new_user_id: int, referrer_id: int, context: ContextTypes.DEFAULT_TYPE):
    config = await get_config()

    new_user_doc = await get_user_referral_data(new_user_id)
    if not new_user_doc:
        return False

    ref_data = new_user_doc.get("referral", {})

    if ref_data.get("is_banned"):
        return False

    fraud_score = ref_data.get("fraud_score", 0.0)
    threshold = config["fraud"]["score_threshold"]
    if fraud_score >= threshold:
        await flag_fraud_user(new_user_id, "Fraud threshold exceeded at verification", fraud_score)
        return False

    join_ts = ref_data.get("join_timestamp", time.time())
    stay_seconds = time.time() - join_ts
    min_stay = config["verification"]["min_stay_seconds"]
    if stay_seconds < min_stay:
        return False

    msg_count = ref_data.get("message_count", 0)
    min_msgs = config["verification"]["min_messages"]
    if msg_count < min_msgs:
        return False

    min_age_days = config["verification"]["min_account_age_days"]
    if min_age_days > 0:
        first_seen = ref_data.get("first_seen", time.time())
        age_days = (time.time() - first_seen) / 86400
        if age_days < min_age_days:
            return False

    referrer_doc = await user_collection.find_one_and_update(
        {
            "id": referrer_id,
            "referral.pending_referrals": new_user_id,
            "referral.fraud_flagged": {"$ne": True},
        },
        {
            "$pull": {"referral.pending_referrals": new_user_id},
            "$addToSet": {"referral.referrals": new_user_id},
            "$inc": {
                "referral.referral_count": 1,
                "referral.verified_referrals": 1,
            },
        },
        return_document=True,
        projection={"referral": 1, "id": 1},
    )

    if not referrer_doc:
        return False

    referrer_ref = referrer_doc.get("referral", {})
    verified_count = referrer_ref.get("verified_referrals", 0)
    current_streak = referrer_ref.get("streak", 0)
    longest_streak = referrer_ref.get("longest_streak", 0)
    last_date_str = referrer_ref.get("last_referral_date")
    today_str = get_utc_date_str()

    new_streak = 1
    if last_date_str:
        yesterday_str = get_utc_date_str(time.time() - 86400)
        if last_date_str == yesterday_str:
            new_streak = current_streak + 1
        elif last_date_str == today_str:
            new_streak = current_streak
        else:
            new_streak = 1
    else:
        new_streak = 1

    new_longest = max(longest_streak, new_streak)
    new_tier = get_tier(verified_count, config)
    reward, multiplier = calculate_reward(new_tier, new_streak, config)

    history_entry = {
        "user_id": new_user_id,
        "timestamp": int(time.time()),
        "reward": reward,
        "streak_bonus": int(reward * (multiplier - 1)),
    }

    update_fields = {
        "referral.streak": new_streak,
        "referral.longest_streak": new_longest,
        "referral.last_referral_date": today_str,
        "referral.tier": new_tier,
    }

    await user_collection.update_one(
        {"id": referrer_id},
        {
            "$set": update_fields,
            "$inc": {"referral.total_earned": reward},
            "$push": {
                "referral.referral_history": {
                    "$each": [history_entry],
                    "$slice": -100,
                }
            },
        },
    )

    await user_collection.update_one(
        {"id": new_user_id},
        {"$set": {"referral.is_verified": True}},
    )

    await change_balance(referrer_id, reward)

    LOGGER.info(
        f"Referral verified: referrer={referrer_id}, new_user={new_user_id}, "
        f"reward={reward}, streak={new_streak}, tier={new_tier}"
    )

    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=(
                f"✨ <b>{to_small_caps('referral verified!')}</b>\n\n"
                f"ᴜsᴇʀ <code>{new_user_id}</code> ʜᴀs ʙᴇᴇɴ ᴠᴇʀɪғɪᴇᴅ!\n"
                f"ʀᴇᴡᴀʀᴅ: <b>+{reward} ᴄᴏɪɴs</b>\n"
                f"sᴛʀᴇᴀᴋ: <b>{new_streak} ᴅᴀʏs</b>\n"
                f"ᴍᴜʟᴛɪᴘʟɪᴇʀ: <b>×{multiplier}</b>\n"
                f"ᴛɪᴇʀ: <b>{new_tier}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        LOGGER.warning(f"Could not notify referrer {referrer_id}: {e}")

    return True


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "⚠️ ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.",
            parse_mode="HTML",
        )
        return

    user = update.effective_user
    user_id = user.id

    await ensure_referral_schema(user_id, user.username, user.first_name)

    config = await get_config()
    doc = await get_user_referral_data(user_id)

    if not doc:
        await update.message.reply_text("⚠️ ᴇʀʀᴏʀ ғᴇᴛᴄʜɪɴɢ ʏᴏᴜʀ ᴅᴀᴛᴀ.")
        return

    ref = doc.get("referral", {})
    code = ref.get("code", generate_referral_code(user_id))
    tier = ref.get("tier", "Bronze")
    streak = ref.get("streak", 0)
    longest = ref.get("longest_streak", 0)
    verified = ref.get("verified_referrals", 0)
    pending = len(ref.get("pending_referrals", []))
    total_earned = ref.get("total_earned", 0)
    fraud_score = ref.get("fraud_score", 0.0)

    thresholds = config["tier_thresholds"]
    tier_order = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
    current_idx = tier_order.index(tier)
    if current_idx < len(tier_order) - 1:
        next_tier = tier_order[current_idx + 1]
        next_threshold = thresholds[next_tier]
        remaining = next_threshold - verified
        progress_text = f"↳ <b>{remaining}</b> ᴍᴏʀᴇ ʀᴇғᴇʀʀᴀʟs ᴛᴏ <b>{next_tier}</b>"
    else:
        progress_text = "↳ ᴍᴀx ᴛɪᴇʀ ʀᴇᴀᴄʜᴇᴅ 🏆"

    fraud_indicator = ""
    if fraud_score >= config["fraud"]["score_threshold"] * 0.7:
        fraud_indicator = "⚠️ ʜɪɢʜ"
    elif fraud_score > 0:
        fraud_indicator = "🟡 ᴍᴇᴅɪᴜᴍ"
    else:
        fraud_indicator = "🟢 ᴄʟᴇᴀɴ"

    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"
    base_reward = get_base_reward(tier, config)
    multiplier = get_streak_multiplier(streak, config)
    current_reward = int(base_reward * multiplier)

    text = (
        f"✦ <b>{to_small_caps('your referral dashboard')}</b> ✦\n\n"
        f"🔗 <b>{to_small_caps('referral link')}</b>\n"
        f"↳ <code>{referral_link}</code>\n\n"
        f"🎫 <b>{to_small_caps('code')}</b>: <code>{code}</code>\n\n"
        f"🏅 <b>{to_small_caps('tier')}</b>: <b>{tier}</b>\n"
        f"⚡ <b>{to_small_caps('streak')}</b>: {streak} ᴅᴀʏs\n"
        f"🔥 <b>{to_small_caps('longest streak')}</b>: {longest} ᴅᴀʏs\n\n"
        f"✅ <b>{to_small_caps('verified referrals')}</b>: {verified}\n"
        f"⏳ <b>{to_small_caps('pending referrals')}</b>: {pending}\n"
        f"💰 <b>{to_small_caps('total earned')}</b>: {total_earned} ᴄᴏɪɴs\n\n"
        f"🎯 <b>{to_small_caps('current reward')}</b>: {current_reward} ᴄᴏɪɴs (×{multiplier})\n\n"
        f"📈 <b>{to_small_caps('tier progress')}</b>\n{progress_text}\n\n"
        f"🛡 <b>{to_small_caps('trust score')}</b>: {fraud_indicator}"
    )

    keyboard = [
        [InlineKeyboardButton("📤 sʜᴀʀᴇ ʀᴇғᴇʀʀᴀʟ", url=f"https://t.me/share/url?url={referral_link}&text=Join+via+my+referral!")],
        [
            InlineKeyboardButton("🏆 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ", callback_data="ref_leaderboard_0"),
            InlineKeyboardButton("🔥 ᴛᴏᴘ sᴛʀᴇᴀᴋ", callback_data="ref_topstreak_0"),
        ],
        [InlineKeyboardButton("🎁 ʀᴇᴡᴀʀᴅ ɪɴғᴏ", callback_data="ref_rewards")],
    ]

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


async def topref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = 0
    if context.args:
        try:
            page = max(0, int(context.args[0]) - 1)
        except ValueError:
            page = 0

    await send_topref_page(update, context, page, edit=False)


async def send_topref_page(update_or_query, context, page: int, edit: bool = False):
    limit = 10
    skip = page * limit

    pipeline = [
        {"$match": {"referral.verified_referrals": {"$gt": 0}}},
        {"$sort": {"referral.verified_referrals": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {"id": 1, "first_name": 1, "username": 1, "referral.verified_referrals": 1, "referral.tier": 1}},
    ]

    cursor = user_collection.aggregate(pipeline)
    users = await cursor.to_list(length=limit)

    if not users:
        text = "📊 <b>ɴᴏ ᴅᴀᴛᴀ ʏᴇᴛ.</b>"
        if edit:
            await update_or_query.edit_message_text(text, parse_mode="HTML")
        else:
            await update_or_query.message.reply_text(text, parse_mode="HTML")
        return

    tier_medals = {"Diamond": "💎", "Platinum": "🏅", "Gold": "🥇", "Silver": "🥈", "Bronze": "🥉"}
    lines = [f"✦ <b>{to_small_caps('top referrers')}</b> ✦\n"]

    for i, u in enumerate(users, start=skip + 1):
        name = escape(u.get("first_name") or "User")
        uid = u["id"]
        ref_count = u.get("referral", {}).get("verified_referrals", 0)
        tier = u.get("referral", {}).get("tier", "Bronze")
        medal = tier_medals.get(tier, "🥉")
        lines.append(f"{i}. {medal} <a href='tg://user?id={uid}'>{name}</a> — <b>{ref_count}</b> ʀᴇғᴇʀʀᴀʟs")

    text = "\n".join(lines)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀ ᴘʀᴇᴠ", callback_data=f"ref_leaderboard_{page - 1}"))
    if len(users) == limit:
        nav_buttons.append(InlineKeyboardButton("ɴᴇxᴛ ▶", callback_data=f"ref_leaderboard_{page + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if edit:
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def topstreak_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = 0
    if context.args:
        try:
            page = max(0, int(context.args[0]) - 1)
        except ValueError:
            page = 0

    await send_topstreak_page(update, context, page, edit=False)


async def send_topstreak_page(update_or_query, context, page: int, edit: bool = False):
    limit = 10
    skip = page * limit

    pipeline = [
        {"$match": {"referral.longest_streak": {"$gt": 0}}},
        {"$sort": {"referral.longest_streak": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$project": {"id": 1, "first_name": 1, "referral.longest_streak": 1, "referral.streak": 1}},
    ]

    cursor = user_collection.aggregate(pipeline)
    users = await cursor.to_list(length=limit)

    if not users:
        text = "📊 <b>ɴᴏ sᴛʀᴇᴀᴋ ᴅᴀᴛᴀ ʏᴇᴛ.</b>"
        if edit:
            await update_or_query.edit_message_text(text, parse_mode="HTML")
        else:
            await update_or_query.message.reply_text(text, parse_mode="HTML")
        return

    streak_emojis = ["🔥", "⚡", "✨", "💫", "🌟"]
    lines = [f"✦ <b>{to_small_caps('top streaks')}</b> ✦\n"]

    for i, u in enumerate(users, start=skip + 1):
        name = escape(u.get("first_name") or "User")
        uid = u["id"]
        longest = u.get("referral", {}).get("longest_streak", 0)
        current = u.get("referral", {}).get("streak", 0)
        emoji = streak_emojis[min(i - 1, len(streak_emojis) - 1)]
        lines.append(f"{i}. {emoji} <a href='tg://user?id={uid}'>{name}</a> — <b>{longest}</b> ᴅᴀʏs (ᴄᴜʀʀᴇɴᴛ: {current})")

    text = "\n".join(lines)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀ ᴘʀᴇᴠ", callback_data=f"ref_topstreak_{page - 1}"))
    if len(users) == limit:
        nav_buttons.append(InlineKeyboardButton("ɴᴇxᴛ ▶", callback_data=f"ref_topstreak_{page + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if edit:
        await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def refstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and user.id not in SUDO_USERS:
        await update.message.reply_text("⛔ ᴀᴅᴍɪɴ ᴏɴʟʏ.")
        return

    total_refs_pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$referral.verified_referrals"}}},
    ]
    total_rewards_pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$referral.total_earned"}}},
    ]
    fraud_count_pipeline = [
        {"$match": {"referral.fraud_flagged": True}},
        {"$count": "count"},
    ]
    avg_streak_pipeline = [
        {"$match": {"referral.streak": {"$gt": 0}}},
        {"$group": {"_id": None, "avg": {"$avg": "$referral.streak"}}},
    ]
    tier_distribution_pipeline = [
        {"$match": {"referral.tier": {"$exists": True}}},
        {"$group": {"_id": "$referral.tier", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]

    async def run_agg(pipeline):
        cursor = user_collection.aggregate(pipeline)
        return await cursor.to_list(length=100)

    results = await asyncio.gather(
        run_agg(total_refs_pipeline),
        run_agg(total_rewards_pipeline),
        run_agg(fraud_count_pipeline),
        run_agg(avg_streak_pipeline),
        run_agg(tier_distribution_pipeline),
    )

    total_refs = results[0][0]["total"] if results[0] else 0
    total_rewards = results[1][0]["total"] if results[1] else 0
    fraud_count = results[2][0]["count"] if results[2] else 0
    avg_streak = round(results[3][0]["avg"], 2) if results[3] else 0
    tier_dist = results[4]

    tier_lines = "\n".join(
        f"  {d['_id']}: <b>{d['count']}</b>" for d in tier_dist
    ) or "  ɴ/ᴀ"

    text = (
        f"✦ <b>{to_small_caps('global referral analytics')}</b> ✦\n\n"
        f"📊 ᴛᴏᴛᴀʟ ᴠᴇʀɪғɪᴇᴅ ʀᴇғᴇʀʀᴀʟs: <b>{total_refs}</b>\n"
        f"💰 ᴛᴏᴛᴀʟ ʀᴇᴡᴀʀᴅs ᴘᴀɪᴅ: <b>{total_rewards} ᴄᴏɪɴs</b>\n"
        f"🚨 ғʀᴀᴜᴅ ғʟᴀɢɢᴇᴅ ᴜsᴇʀs: <b>{fraud_count}</b>\n"
        f"🔥 ᴀᴠɢ sᴛʀᴇᴀᴋ: <b>{avg_streak} ᴅᴀʏs</b>\n\n"
        f"🏅 ᴛɪᴇʀ ᴅɪsᴛʀɪʙᴜᴛɪᴏɴ:\n{tier_lines}"
    )

    await update.message.reply_text(text, parse_mode="HTML")


async def setrefconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID and user.id not in SUDO_USERS:
        await update.message.reply_text("⛔ ᴀᴅᴍɪɴ ᴏɴʟʏ.")
        return

    if not context.args or len(context.args) < 2:
        text = (
            f"✦ <b>{to_small_caps('referral config editor')}</b> ✦\n\n"
            f"<b>ᴜsᴀɢᴇ:</b>\n"
            f"/setrefconfig base_reward Bronze 50\n"
            f"/setrefconfig base_reward Silver 60\n"
            f"/setrefconfig tier_threshold Silver 10\n"
            f"/setrefconfig streak_multiplier 7 2.0\n"
            f"/setrefconfig fraud_threshold 10.0\n"
            f"/setrefconfig min_messages 3\n"
            f"/setrefconfig min_stay 300\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")
        return

    key = context.args[0].lower()

    try:
        config = await get_config()

        if key == "base_reward" and len(context.args) == 3:
            tier_name = context.args[1].capitalize()
            value = int(context.args[2])
            config["base_rewards"][tier_name] = value

        elif key == "tier_threshold" and len(context.args) == 3:
            tier_name = context.args[1].capitalize()
            value = int(context.args[2])
            config["tier_thresholds"][tier_name] = value

        elif key == "streak_multiplier" and len(context.args) == 3:
            days = int(context.args[1])
            value = float(context.args[2])
            config["streak_multipliers"][days] = value

        elif key == "fraud_threshold" and len(context.args) == 2:
            config["fraud"]["score_threshold"] = float(context.args[1])

        elif key == "min_messages" and len(context.args) == 2:
            config["verification"]["min_messages"] = int(context.args[1])

        elif key == "min_stay" and len(context.args) == 2:
            config["verification"]["min_stay_seconds"] = int(context.args[1])

        else:
            await update.message.reply_text("⚠️ ɪɴᴠᴀʟɪᴅ ᴄᴏɴғɪɢ ᴋᴇʏ ᴏʀ ᴀʀɢᴜᴍᴇɴᴛs.")
            return

        config_to_save = config.copy()
        await REFERRAL_CONFIG_COLLECTION.update_one(
            {"_id": "main"},
            {"$set": config_to_save},
            upsert=True,
        )
        await invalidate_config_cache()

        await update.message.reply_text(
            f"✅ <b>ᴄᴏɴғɪɢ ᴜᴘᴅᴀᴛᴇᴅ:</b> <code>{key}</code>",
            parse_mode="HTML",
        )

    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"⚠️ ɪɴᴠᴀʟɪᴅ ᴠᴀʟᴜᴇ: {escape(str(e))}", parse_mode="HTML")
    except Exception as e:
        LOGGER.error(f"setrefconfig error: {e}")
        await update.message.reply_text("⚠️ ᴇʀʀᴏʀ ᴜᴘᴅᴀᴛɪɴɢ ᴄᴏɴғɪɢ.")


async def referral_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("ref_leaderboard_"):
        page = int(data.split("_")[-1])
        await send_topref_page(query, context, page, edit=True)

    elif data.startswith("ref_topstreak_"):
        page = int(data.split("_")[-1])
        await send_topstreak_page(query, context, page, edit=True)

    elif data == "ref_rewards":
        config = await get_config()
        rewards = config["base_rewards"]
        multipliers = config["streak_multipliers"]

        reward_lines = "\n".join(
            f"  {tier}: <b>{coins} ᴄᴏɪɴs</b>" for tier, coins in rewards.items()
        )
        mult_lines = "\n".join(
            f"  {days}+ ᴅᴀʏs: <b>×{mult}</b>" for days, mult in sorted(multipliers.items(), key=lambda x: x[0])
        )

        text = (
            f"✦ <b>{to_small_caps('reward structure')}</b> ✦\n\n"
            f"🏅 <b>{to_small_caps('base rewards by tier')}</b>\n{reward_lines}\n\n"
            f"🔥 <b>{to_small_caps('streak multipliers')}</b>\n{mult_lines}"
        )

        back_keyboard = [[InlineKeyboardButton("◀ ʙᴀᴄᴋ", callback_data="ref_back")]]
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(back_keyboard),
        )

    elif data == "ref_back":
        user = query.from_user
        user_id = user.id
        config = await get_config()
        doc = await get_user_referral_data(user_id)

        if not doc:
            await query.edit_message_text("⚠️ ᴇʀʀᴏʀ.", parse_mode="HTML")
            return

        ref = doc.get("referral", {})
        code = ref.get("code", generate_referral_code(user_id))
        referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"

        keyboard = [
            [InlineKeyboardButton("📤 sʜᴀʀᴇ ʀᴇғᴇʀʀᴀʟ", url=f"https://t.me/share/url?url={referral_link}&text=Join+via+my+referral!")],
            [
                InlineKeyboardButton("🏆 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ", callback_data="ref_leaderboard_0"),
                InlineKeyboardButton("🔥 ᴛᴏᴘ sᴛʀᴇᴀᴋ", callback_data="ref_topstreak_0"),
            ],
            [InlineKeyboardButton("🎁 ʀᴇᴡᴀʀᴅ ɪɴғᴏ", callback_data="ref_rewards")],
        ]

        tier = ref.get("tier", "Bronze")
        streak = ref.get("streak", 0)
        verified = ref.get("verified_referrals", 0)
        pending = len(ref.get("pending_referrals", []))
        total_earned = ref.get("total_earned", 0)
        fraud_score = ref.get("fraud_score", 0.0)

        thresholds = config["tier_thresholds"]
        tier_order = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
        current_idx = tier_order.index(tier)
        if current_idx < len(tier_order) - 1:
            next_tier = tier_order[current_idx + 1]
            next_threshold = thresholds[next_tier]
            remaining = next_threshold - verified
            progress_text = f"↳ <b>{remaining}</b> ᴍᴏʀᴇ ʀᴇғᴇʀʀᴀʟs ᴛᴏ <b>{next_tier}</b>"
        else:
            progress_text = "↳ ᴍᴀx ᴛɪᴇʀ ʀᴇᴀᴄʜᴇᴅ 🏆"

        if fraud_score >= config["fraud"]["score_threshold"] * 0.7:
            fraud_indicator = "⚠️ ʜɪɢʜ"
        elif fraud_score > 0:
            fraud_indicator = "🟡 ᴍᴇᴅɪᴜᴍ"
        else:
            fraud_indicator = "🟢 ᴄʟᴇᴀɴ"

        base_reward = get_base_reward(tier, config)
        multiplier = get_streak_multiplier(streak, config)
        current_reward = int(base_reward * multiplier)
        longest = ref.get("longest_streak", 0)

        text = (
            f"✦ <b>{to_small_caps('your referral dashboard')}</b> ✦\n\n"
            f"🔗 <b>{to_small_caps('referral link')}</b>\n"
            f"↳ <code>{referral_link}</code>\n\n"
            f"🎫 <b>{to_small_caps('code')}</b>: <code>{code}</code>\n\n"
            f"🏅 <b>{to_small_caps('tier')}</b>: <b>{tier}</b>\n"
            f"⚡ <b>{to_small_caps('streak')}</b>: {streak} ᴅᴀʏs\n"
            f"🔥 <b>{to_small_caps('longest streak')}</b>: {longest} ᴅᴀʏs\n\n"
            f"✅ <b>{to_small_caps('verified referrals')}</b>: {verified}\n"
            f"⏳ <b>{to_small_caps('pending referrals')}</b>: {pending}\n"
            f"💰 <b>{to_small_caps('total earned')}</b>: {total_earned} ᴄᴏɪɴs\n\n"
            f"🎯 <b>{to_small_caps('current reward')}</b>: {current_reward} ᴄᴏɪɴs (×{multiplier})\n\n"
            f"📈 <b>{to_small_caps('tier progress')}</b>\n{progress_text}\n\n"
            f"🛡 <b>{to_small_caps('trust score')}</b>: {fraud_indicator}"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )


async def background_verification_task(context: ContextTypes.DEFAULT_TYPE):
    config = await get_config()
    max_pending_hours = config["verification"]["max_pending_age_hours"]
    expiry_threshold = time.time() - (max_pending_hours * 3600)

    pipeline = [
        {"$match": {"referral.pending_referrals": {"$exists": True, "$ne": []}}},
        {"$project": {"id": 1, "referral.pending_referrals": 1}},
        {"$limit": 500},
    ]

    cursor = user_collection.aggregate(pipeline)
    referrers = await cursor.to_list(length=500)

    for referrer in referrers:
        referrer_id = referrer["id"]
        pending_ids = referrer.get("referral", {}).get("pending_referrals", [])

        for pending_user_id in pending_ids[:]:
            pending_doc = await user_collection.find_one(
                {"id": pending_user_id},
                {"referral.join_timestamp": 1, "referral.is_verified": 1, "referral.message_count": 1},
            )

            if not pending_doc:
                await user_collection.update_one(
                    {"id": referrer_id},
                    {"$pull": {"referral.pending_referrals": pending_user_id}},
                )
                continue

            ref_data = pending_doc.get("referral", {})
            join_ts = ref_data.get("join_timestamp", 0)

            if join_ts < expiry_threshold:
                await user_collection.update_one(
                    {"id": referrer_id},
                    {"$pull": {"referral.pending_referrals": pending_user_id}},
                )
                LOGGER.info(f"Expired pending referral: {pending_user_id} from referrer {referrer_id}")
                continue

            if ref_data.get("is_verified"):
                continue

            verified = await verify_and_reward_referral(pending_user_id, referrer_id, context)
            if verified:
                LOGGER.info(f"Background verification success: {pending_user_id} → {referrer_id}")

    today_str = get_utc_date_str()
    yesterday_str = get_utc_date_str(time.time() - 86400)

    expired_streak_pipeline = [
        {
            "$match": {
                "referral.streak": {"$gt": 0},
                "referral.last_referral_date": {
                    "$exists": True,
                    "$nin": [today_str, yesterday_str],
                },
            }
        },
        {"$project": {"id": 1}},
        {"$limit": 1000},
    ]

    cursor = user_collection.aggregate(expired_streak_pipeline)
    expired_users = await cursor.to_list(length=1000)

    if expired_users:
        expired_ids = [u["id"] for u in expired_users]
        await user_collection.update_many(
            {"id": {"$in": expired_ids}},
            {"$set": {"referral.streak": 0}},
        )
        LOGGER.info(f"Reset streaks for {len(expired_ids)} users")


async def start_referral_background_job(application):
    job_queue = application.job_queue
    job_queue.run_repeating(
        background_verification_task,
        interval=300,
        first=60,
        name="referral_verifier",
    )


application.add_handler(CommandHandler("refer", referral_command))
application.add_handler(CommandHandler("topref", topref_command))
application.add_handler(CommandHandler("topstreak", topstreak_command))
application.add_handler(CommandHandler("refstats", refstats_command))
application.add_handler(CommandHandler("refc", setrefconfig_command))
application.add_handler(
    CallbackQueryHandler(
        referral_callback_handler,
        pattern=r"^ref_(leaderboard|topstreak|rewards|back)(_\d+)?$",
    )
)
