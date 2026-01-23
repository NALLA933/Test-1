import time
import math
import random
from html import escape
from typing import Optional, Dict, Any, List

from telegram import Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, ContextTypes

from pymongo import ReturnDocument

from shivu import application, db, LOGGER, OWNER_ID, SUDO_USERS

# Collections
user_balance_coll = db.get_collection("user_balance")         # documents: { user_id, balance, last_daily, items: [...] }
currency_tx_coll = db.get_collection("currency_transactions") # optional audit log

# Configuration
DAILY_REWARD_MIN = 50
DAILY_REWARD_MAX = 150
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
TOP_N = 10


# ---------- Helpers ----------
async def _ensure_balance_doc(user_id: int) -> Dict[str, Any]:
    """Ensure a balance document exists for the user and return it."""
    # Upsert a minimal document if missing
    try:
        await user_balance_coll.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id, "balance": 0, "last_daily": 0, "items": []}},
            upsert=True,
        )
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return doc or {"user_id": user_id, "balance": 0, "last_daily": 0, "items": []}
    except Exception:
        LOGGER.exception("Error ensuring balance doc for %s", user_id)
        # Best effort fallback
        return {"user_id": user_id, "balance": 0, "last_daily": 0, "items": []}


async def get_balance(user_id: int) -> int:
    """Return integer balance for a user."""
    doc = await _ensure_balance_doc(user_id)
    return int(doc.get("balance", 0))


async def change_balance(user_id: int, amount: int, reason: Optional[str] = None) -> int:
    """
    Atomically change balance by `amount` (positive or negative).
    Returns the new balance after change.
    """
    if amount == 0:
        return await get_balance(user_id)

    try:
        await user_balance_coll.update_one({"user_id": user_id}, {"$inc": {"balance": int(amount)}}, upsert=True)
        # write audit (best-effort)
        try:
            await currency_tx_coll.insert_one({"user_id": user_id, "amount": int(amount), "reason": reason or "", "ts": int(time.time())})
        except Exception:
            LOGGER.debug("currency tx insert failed for user %s", user_id)
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return int(doc.get("balance", 0)) if doc else 0
    except Exception:
        LOGGER.exception("Failed to change balance for %s by %s", user_id, amount)
        raise


# ---------- Command handlers ----------
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /balance [@username|id] or reply
    Show balance for yourself or given user.
    """
    target = update.effective_user
    # Try to resolve argument or reply target
    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            try:
                # fetch chat to get display name
                target = await context.bot.get_chat(int(arg))
            except Exception:
                target = update.effective_user
        elif arg.startswith("@"):
            try:
                target = await context.bot.get_chat(arg)
            except Exception:
                target = update.effective_user
    elif update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user

    user_id = getattr(target, "id", update.effective_user.id)
    bal = await get_balance(user_id)
    name = escape(getattr(target, "first_name", str(user_id)))
    await update.message.reply_text(f"💰 <b>{name}</b>'s Balance: <b>{bal:,}</b> coins", parse_mode="HTML")


async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /daily - claim daily reward (24h cooldown)
    Uses user's last_daily timestamp to enforce cooldown.
    """
    user = update.effective_user
    doc = await _ensure_balance_doc(user.id)
    now = int(time.time())
    last = int(doc.get("last_daily", 0) or 0)
    if now - last < DAILY_COOLDOWN_SECONDS:
        remaining = DAILY_COOLDOWN_SECONDS - (now - last)
        hrs = remaining // 3600
        mins = (remaining % 3600) // 60
        secs = remaining % 60
        await update.message.reply_text(f"⏳ You already claimed daily. Come back in {hrs}h {mins}m {secs}s.")
        return

    reward = random.randint(DAILY_REWARD_MIN, DAILY_REWARD_MAX)

    # Apply simple item-based bonus if present
    items: List[str] = doc.get("items", [])
    if "lucky_token" in items:
        reward = int(reward * 1.2)

    try:
        await user_balance_coll.update_one(
            {"user_id": user.id},
            {"$inc": {"balance": reward}, "$set": {"last_daily": now}},
            upsert=True,
        )
        try:
            await currency_tx_coll.insert_one({"user_id": user.id, "amount": reward, "reason": "daily", "ts": now})
        except Exception:
            LOGGER.debug("Failed to log daily tx for %s", user.id)
    except Exception:
        LOGGER.exception("Failed to grant daily to %s", user.id)
        await update.message.reply_text("❌ Failed to grant daily reward. Try again later.")
        return

    await update.message.reply_text(f"🎉 You claimed your daily reward: <b>{reward:,}</b> coins!", parse_mode="HTML")


async def _atomic_transfer(sender_id: int, receiver_id: int, amount: int) -> bool:
    """
    Atomically transfer coins from sender -> receiver using a conditional decrement
    and compensating rollback on unexpected failures. Returns True on success.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    # Step 1: decrement sender only if they have enough balance
    try:
        sender_after = await user_balance_coll.find_one_and_update(
            {"user_id": sender_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        LOGGER.exception("Error decrementing balance for sender %s", sender_id)
        return False

    if sender_after is None:
        # insufficient funds
        return False

    # Step 2: increment receiver
    try:
        await user_balance_coll.update_one({"user_id": receiver_id}, {"$inc": {"balance": amount}}, upsert=True)
        # audit
        try:
            await currency_tx_coll.insert_one({"user_id": sender_id, "amount": -amount, "reason": f"pay:{receiver_id}", "ts": int(time.time())})
            await currency_tx_coll.insert_one({"user_id": receiver_id, "amount": amount, "reason": f"receive:{sender_id}", "ts": int(time.time())})
        except Exception:
            LOGGER.debug("Failed to write transfer audit for %s->%s", sender_id, receiver_id)
        return True
    except Exception:
        LOGGER.exception("Failed to increment receiver %s; attempting rollback to sender %s", receiver_id, sender_id)
        # rollback: try to re-add amount to sender
        try:
            await user_balance_coll.update_one({"user_id": sender_id}, {"$inc": {"balance": amount}}, upsert=True)
        except Exception:
            LOGGER.exception("Rollback failed for sender %s after transfer failure", sender_id)
        return False


async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pay <user_id|@username|reply> <amount>
    Transfer coins to another user with atomic conditional decrement.
    """
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("Usage: /pay <user_id|@username> <amount>  (or reply with /pay <amount>)")
        return

    sender = update.effective_user

    # Resolve target
    target_id: Optional[int] = None
    amount_str: Optional[str] = None

    if update.message.reply_to_message and len(context.args) == 1:
        # /pay <amount> as a reply
        target_id = update.message.reply_to_message.from_user.id
        amount_str = context.args[0]
    else:
        # /pay <target> <amount>
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /pay <user_id|@username|reply> <amount>")
            return
        raw_target = context.args[0]
        amount_str = context.args[1]
        if raw_target.isdigit():
            target_id = int(raw_target)
        elif raw_target.startswith("@"):
            try:
                chat = await context.bot.get_chat(raw_target)
                target_id = chat.id
            except Exception:
                target_id = None
        else:
            # try resolve by mention or fallback
            target_id = None

    if not target_id:
        await update.message.reply_text("Could not resolve target user. Use user id, @username or reply to their message.")
        return

    if target_id == sender.id:
        await update.message.reply_text("You cannot pay yourself.")
        return

    try:
        amount = int(amount_str)
    except Exception:
        await update.message.reply_text("Invalid amount. Use a positive integer.")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than zero.")
        return

    # Attempt atomic transfer
    success = await _atomic_transfer(sender.id, target_id, amount)
    if not success:
        await update.message.reply_text("❌ Transaction failed: insufficient funds or internal error.")
        return

    # Success
    await update.message.reply_text(
        f"✅ Sent <b>{amount:,}</b> coins to <a href='tg://user?id={target_id}'>user</a>.",
        parse_mode="HTML"
    )


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /top - show top balances (global)
    """
    try:
        cursor = user_balance_coll.find({}, {"user_id": 1, "balance": 1}).sort("balance", -1).limit(TOP_N)
        leaders = await cursor.to_list(length=TOP_N)
    except Exception:
        LOGGER.exception("Leaderboard fetch failed")
        await update.message.reply_text("Could not fetch leaderboard right now.")
        return

    if not leaders:
        await update.message.reply_text("No balances found yet.")
        return

    msg = "<b>🏆 Top balances</b>\n\n"
    for i, doc in enumerate(leaders, start=1):
        uid = doc.get("user_id")
        bal = int(doc.get("balance", 0))
        # try to get a friendly name
        name = str(uid)
        try:
            chat = await context.bot.get_chat(uid)
            name = escape(getattr(chat, "first_name", str(uid)))
        except Exception:
            name = str(uid)
        msg += f"{i}. <b>{name}</b> — <code>{bal:,}</code>\n"
    await update.message.reply_text(msg, parse_mode="HTML")


async def admin_give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /give <user_id> <amount> - admin-only adjust balance
    Restricted to OWNER_ID and SUDO_USERS.
    """
    user_id = update.effective_user.id
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text("Not authorized.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /give <user_id> <amount>")
        return

    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid arguments.")
        return

    try:
        new_bal = await change_balance(target, amount, reason=f"admin:{user_id}")
        await update.message.reply_text(f"Updated balance for <a href='tg://user?id={target}'>user</a>: <b>{new_bal:,}</b>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("Failed to update balance.")


# Register handlers
application.add_handler(CommandHandler(["balance", "bal"], balance_cmd, block=False))
application.add_handler(CommandHandler("daily", daily_cmd, block=False))
application.add_handler(CommandHandler("pay", pay_cmd, block=False))
application.add_handler(CommandHandler(["topbal", "top"], top_cmd, block=False))
application.add_handler(CommandHandler("give", admin_give_cmd, block=False))
