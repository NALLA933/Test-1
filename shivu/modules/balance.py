import time
import uuid
from html import escape
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatType

from pymongo import ReturnDocument

from shivu import application, db, LOGGER, OWNER_ID, SUDO_USERS

# Collections
user_balance_coll = db.get_collection("user_balance")
user_collection = db.get_collection("users")

# In-memory pending payments and cooldowns
pending_payments: Dict[str, Dict[str, Any]] = {}
pay_cooldowns: Dict[int, float] = {}

# Configuration
PENDING_EXPIRY_SECONDS = 5 * 60
PAY_COOLDOWN_SECONDS = 60
BOT_START_URL = "https://t.me/Senpai_Waifu_Grabbing_Bot?start=_tgr_1tTPLUQwNjI1"


async def _ensure_balance_doc(user_id: int) -> Dict[str, Any]:
    try:
        await user_balance_coll.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id, "balance": 0}},
            upsert=True,
        )
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return doc or {"user_id": user_id, "balance": 0}
    except Exception:
        LOGGER.exception("Error ensuring balance doc for %s", user_id)
        return {"user_id": user_id, "balance": 0}


async def get_balance(user_id: int) -> int:
    doc = await _ensure_balance_doc(user_id)
    return int(doc.get("balance", 0))


async def change_balance(user_id: int, amount: int) -> int:
    if amount == 0:
        return await get_balance(user_id)

    try:
        await user_balance_coll.update_one({"user_id": user_id}, {"$inc": {"balance": int(amount)}}, upsert=True)
        doc = await user_balance_coll.find_one({"user_id": user_id})
        return int(doc.get("balance", 0)) if doc else 0
    except Exception:
        LOGGER.exception("Failed to change balance for %s by %s", user_id, amount)
        raise


async def _atomic_transfer(sender_id: int, receiver_id: int, amount: int) -> bool:
    if amount <= 0:
        return False

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
        return False

    try:
        await user_balance_coll.update_one({"user_id": receiver_id}, {"$inc": {"balance": amount}}, upsert=True)
        return True
    except Exception:
        LOGGER.exception("Failed to increment receiver %s; attempting rollback to sender %s", receiver_id, sender_id)
        try:
            await user_balance_coll.update_one({"user_id": sender_id}, {"$inc": {"balance": amount}}, upsert=True)
        except Exception:
            LOGGER.exception("Rollback failed for sender %s after transfer failure", sender_id)
        return False


async def _has_user_started_bot(user_id: int) -> bool:
    try:
        doc = await user_collection.find_one({"user_id": user_id})
        return doc is not None
    except Exception:
        LOGGER.exception("Error checking if user %s started bot", user_id)
        return False


async def _validate_payment_target(context: ContextTypes.DEFAULT_TYPE, target_id: int) -> tuple[bool, Optional[str], Optional[Chat]]:
    try:
        target_chat = await context.bot.get_chat(target_id)
    except Exception as e:
        LOGGER.exception("Failed to get chat for target %s", target_id)
        return False, "❌ Could not find this user. Please check the user ID.", None

    if target_chat.type != ChatType.PRIVATE:
        if target_chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            return False, "❌ Payments are only allowed to individual users.", None
        elif target_chat.type == ChatType.CHANNEL:
            return False, "❌ Payments are only allowed to individual users.", None
        else:
            return False, "❌ Payments are only allowed to individual users.", None

    if target_chat.is_bot:
        return False, "❌ You can only pay real users, not bots.", None

    return True, None, target_chat


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.effective_user
    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            try:
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


async def pay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("Usage: /pay <user_id|@username> <amount>  (or reply with /pay <amount>)")
        return

    sender = update.effective_user

    now = time.time()
    next_allowed = pay_cooldowns.get(sender.id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        await update.message.reply_text(f"⏳ You must wait {remaining}s before starting another payment.")
        return

    target_id: Optional[int] = None
    amount_str: Optional[str] = None

    if update.message.reply_to_message and len(context.args) == 1:
        target_id = update.message.reply_to_message.from_user.id
        amount_str = context.args[0]
    else:
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

    if not target_id:
        await update.message.reply_text("❌ Could not resolve target user. Use user id, @username or reply to their message.")
        return

    if target_id == sender.id:
        await update.message.reply_text("❌ You cannot pay yourself.")
        return

    is_valid, error_msg, target_chat = await _validate_payment_target(context, target_id)
    if not is_valid:
        await update.message.reply_text(error_msg)
        return

    try:
        amount = int(amount_str)
    except Exception:
        await update.message.reply_text("❌ Invalid amount. Use a positive integer.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than zero.")
        return

    bal = await get_balance(sender.id)
    if bal < amount:
        await update.message.reply_text(f"❌ Insufficient balance. Your balance: <b>{bal:,}</b> coins", parse_mode="HTML")
        return

    receiver_started = await _has_user_started_bot(target_id)
    if not receiver_started:
        target_name = escape(getattr(target_chat, "first_name", str(target_id)))
        text = (
            f"⚠️ <b>{target_name}</b> has not started the bot yet.\n\n"
            f"Ask them to start the bot in DM to receive payments."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Start Bot", url=BOT_START_URL)]
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    token = uuid.uuid4().hex
    created_at = time.time()
    
    target_name = escape(getattr(target_chat, "first_name", str(target_id)))
    sender_name = escape(getattr(sender, "first_name", str(sender.id)))
    
    pending_payments[token] = {
        "sender_id": sender.id,
        "target_id": target_id,
        "amount": amount,
        "created_at": created_at,
        "chat_id": update.effective_chat.id,
        "sender_name": sender_name,
        "target_name": target_name,
    }

    text = (
        f"⚠️ <b>Payment Confirmation</b>\n\n"
        f"Sender: <a href='tg://user?id={sender.id}'>{sender_name}</a>\n"
        f"Recipient: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
        f"Amount: <b>{amount:,}</b> coins\n\n"
        f"Are you sure you want to proceed?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"pay_confirm:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"pay_cancel:{token}")
        ]
    ])

    msg = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    pending_payments[token]["message_id"] = msg.message_id


async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("pay_confirm:") and not data.startswith("pay_cancel:"):
        return

    action, token = data.split(":", 1)
    pending = pending_payments.get(token)
    if not pending:
        try:
            await query.edit_message_text("❌ This payment request has expired or is invalid.")
        except Exception:
            pass
        return

    sender_id = pending["sender_id"]
    target_id = pending["target_id"]
    amount = pending["amount"]
    created_at = pending["created_at"]
    sender_name = pending.get("sender_name", str(sender_id))
    target_name = pending.get("target_name", str(target_id))

    user_who_clicked = query.from_user.id
    if user_who_clicked != sender_id:
        await query.answer("⚠️ Only the payment initiator can confirm or cancel this payment.", show_alert=True)
        return

    if time.time() - created_at > PENDING_EXPIRY_SECONDS:
        try:
            await query.edit_message_text("⏳ This payment request has expired.")
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    if action == "pay_cancel":
        try:
            cancelled_text = (
                f"❌ <b>Payment Cancelled</b>\n\n"
                f"Sender: <a href='tg://user?id={sender_id}'>{sender_name}</a>\n"
                f"Recipient: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
                f"Amount: <b>{amount:,}</b> coins\n\n"
                f"The payment was cancelled by the sender."
            )
            await query.edit_message_text(cancelled_text, parse_mode="HTML")
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    try:
        processing_text = (
            f"⏳ <b>Processing Payment...</b>\n\n"
            f"Sender: <a href='tg://user?id={sender_id}'>{sender_name}</a>\n"
            f"Recipient: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
            f"Amount: <b>{amount:,}</b> coins\n\n"
            f"Please wait..."
        )
        processing_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏳ Processing...", callback_data="pay_processing")]
        ])
        await query.edit_message_text(processing_text, parse_mode="HTML", reply_markup=processing_keyboard)
    except Exception:
        pass

    now = time.time()
    next_allowed = pay_cooldowns.get(sender_id, 0)
    if now < next_allowed:
        remaining = int(next_allowed - now)
        await query.edit_message_text(f"⏳ You must wait {remaining}s before making another payment.")
        pending_payments.pop(token, None)
        return

    success = await _atomic_transfer(sender_id, target_id, amount)
    if not success:
        try:
            bal = await get_balance(sender_id)
            await query.edit_message_text(
                f"❌ <b>Transaction Failed</b>\n\n"
                f"Insufficient balance. Your balance: <b>{bal:,}</b> coins",
                parse_mode="HTML"
            )
        except Exception:
            pass
        pending_payments.pop(token, None)
        return

    pay_cooldowns[sender_id] = time.time() + PAY_COOLDOWN_SECONDS

    try:
        confirmed_text = (
            f"✅ <b>Payment Successful</b>\n\n"
            f"Sender: <a href='tg://user?id={sender_id}'>{sender_name}</a>\n"
            f"Recipient: <a href='tg://user?id={target_id}'>{target_name}</a>\n"
            f"Amount: <b>{amount:,}</b> coins\n\n"
            f"Next payment allowed in {PAY_COOLDOWN_SECONDS} seconds."
        )
        await query.edit_message_text(confirmed_text, parse_mode="HTML")
    except Exception:
        pass

    try:
        receiver_notification = (
            f"💰 <b>Payment Received!</b>\n\n"
            f"You received <b>{amount:,}</b> coins from "
            f"<a href='tg://user?id={sender_id}'>{sender_name}</a>."
        )
        await context.bot.send_message(
            chat_id=target_id,
            text=receiver_notification,
            parse_mode="HTML"
        )
    except Exception:
        LOGGER.exception("Failed to send payment notification to receiver %s", target_id)

    pending_payments.pop(token, None)


async def admin_addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != OWNER_ID and user_id not in SUDO_USERS:
        await update.message.reply_text("❌ Not authorized.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return

    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid arguments.")
        return

    try:
        new_bal = await change_balance(target, amount)
        await update.message.reply_text(f"✅ Updated balance for <a href='tg://user?id={target}'>user</a>: <b>{new_bal:,}</b>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Failed to update balance.")


application.add_handler(CommandHandler(["balance", "bal"], balance_cmd, block=False))
application.add_handler(CommandHandler("pay", pay_cmd, block=False))
application.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^pay_", block=False))
application.add_handler(CommandHandler("addbal", admin_addbal_cmd, block=False))