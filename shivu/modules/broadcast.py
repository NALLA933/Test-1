import asyncio
import time
import logging
from datetime import timedelta
from telegram import Update
from telegram.error import Forbidden, BadRequest, RetryAfter, TelegramError
from telegram.ext import CallbackContext, CommandHandler
from shivu import application, top_global_groups_collection, pm_users
from shivu.security import is_owner

logger = logging.getLogger(__name__)

# Broadcast control flag (for cancel feature)
broadcast_running = {'status': False, 'cancel': False}
from shivu.utils import to_small_caps


def create_progress_bar(percentage: float, width: int = 10) -> str:
    """Create a visual progress bar."""
    filled = int(width * percentage / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"


def format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    if seconds <= 0:
        return "0s"
    
    delta = timedelta(seconds=int(seconds))
    parts = []

    if delta.days > 0:
        parts.append(f"{delta.days}d")

    hours = delta.seconds // 3600
    if hours > 0:
        parts.append(f"{hours}h")

    minutes = (delta.seconds % 3600) // 60
    if minutes > 0:
        parts.append(f"{minutes}m")

    secs = delta.seconds % 60
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


async def get_all_recipients() -> tuple[set[int], set[int], set[int], int]:
    """Fetch all unique recipients from both collections and return groups/users separately."""
    all_chats = set()
    all_users = set()
    
    try:
        # Fetch all group IDs with proper error handling
        try:
            async for doc in top_global_groups_collection.find({}, {"group_id": 1}):
                if "group_id" in doc:
                    all_chats.add(doc["group_id"])
            logger.info(f"✅ Fetched {len(all_chats)} groups from database")
        except Exception as e:
            logger.error(f"❌ Error fetching groups: {str(e)}")

        # Fetch all user IDs with proper error handling
        try:
            async for doc in pm_users.find({}, {"_id": 1}):
                if "_id" in doc:
                    all_users.add(doc["_id"])
            logger.info(f"✅ Fetched {len(all_users)} users from database")
        except Exception as e:
            logger.error(f"❌ Error fetching users: {str(e)}")

        # Combine and deduplicate
        all_recipients = all_chats.union(all_users)
        logger.info(f"📊 Total unique recipients: {len(all_recipients)}")
        
        return all_recipients, all_chats, all_users, len(all_recipients)

    except Exception as e:
        logger.exception(f"❌ Critical error in get_all_recipients: {str(e)}")
        raise Exception(f"Failed to fetch recipients: {str(e)}")


async def broadcast(update: Update, context: CallbackContext) -> None:
    """
    Premium broadcast command for owner only (ID: 8420981179).
    
    Usage:
    /broadcast - Send message without forward tag (copy message)
    /broadcast -forward - Send message with forward tag
    """

    # STRICT AUTHORIZATION CHECK - Only user ID 8420981179 can access
    if not is_owner(update.effective_user.id):
        logger.warning(f"⚠️ Unauthorized broadcast attempt by user {update.effective_user.id}")
        await update.message.reply_text(
            "⛔ **ACCESS DENIED**\n\n"
            "🚫 This command is strictly restricted to the bot owner only.\n"
            "🔒 Owner-only command\n\n"
            "Your attempt has been logged."
        )
        return

    # Check if broadcast is already running
    if broadcast_running['status']:
        await update.message.reply_text(
            "⚠️ **Broadcast Already Running**\n\n"
            "Please wait for the current broadcast to complete."
        )
        return

    # Check if message is replied to
    message_to_broadcast = update.effective_message.reply_to_message
    if not message_to_broadcast:
        await update.message.reply_text(
            "📤 **How to use:**\n\n"
            "**Method 1 - Copy Message (No Forward Tag):**\n"
            "1. Reply to any message\n"
            "2. Type `/broadcast`\n\n"
            "**Method 2 - Forward Message (With Forward Tag):**\n"
            "1. Reply to any message\n"
            "2. Type `/broadcast -forward`\n\n"
            "💡 The message will be sent to all users and groups."
        )
        return

    # Check for -forward flag
    use_forward = False
    if context.args and '-forward' in context.args:
        use_forward = True
        logger.info("🔄 Broadcast mode: FORWARD (with forward tag)")
    else:
        logger.info("📋 Broadcast mode: COPY (without forward tag)")

    # Set broadcast running flag
    broadcast_running['status'] = True
    broadcast_running['cancel'] = False

    # Acknowledge command
    processing_msg = await update.message.reply_text(
        "🔄 **Processing...**\n"
        "Fetching recipient list from database..."
    )

    # Get all recipients
    try:
        all_recipients, all_chats, all_users, total_recipients = await get_all_recipients()

        if total_recipients == 0:
            await processing_msg.edit_text("❌ **No recipients found in database.**")
            broadcast_running['status'] = False
            return

        mode_text = "📋 Copy Mode" if not use_forward else "🔄 Forward Mode"
        await processing_msg.edit_text(
            f"✅ **Found {total_recipients:,} recipients**\n"
            f"👥 **Groups:** {len(all_chats):,}\n"
            f"💬 **Users:** {len(all_users):,}\n"
            f"🎯 **Mode:** {mode_text}\n"
            "Starting broadcast in 2 seconds..."
        )
        await asyncio.sleep(2)
        await processing_msg.delete()

    except Exception as e:
        logger.exception(f"❌ Database error: {str(e)}")
        await update.message.reply_text(f"❌ **Database Error:**\n{str(e)}")
        broadcast_running['status'] = False
        return

    # Send initial status message
    mode_emoji = "📋" if not use_forward else "🔄"
    status_msg = await update.message.reply_text(
        f"{mode_emoji} **Broadcast Started**\n\n"
        f"📊 **Progress:** [░░░░░░░░░░] 0.0%\n"
        f"✅ **Sent:** 0/{total_recipients:,}\n"
        f"⛔ **Blocked:** 0\n"
        f"❌ **Failed:** 0\n"
        f"⏱️ **Elapsed:** 0s\n"
        f"⏳ **ETA:** Calculating..."
    )

    # Statistics tracking
    stats = {
        'sent': 0,
        'blocked': 0,
        'failed': 0,
        'groups_sent': 0,
        'users_sent': 0,
        'start_time': time.time(),
        'last_update_time': time.time(),
        'last_update_count': 0,
        'current_index': 0,
        'retry_count': 0
    }

    async def update_status():
        """Update the status message with current progress."""
        current_time = time.time()
        elapsed = current_time - stats['start_time']

        processed = stats['sent'] + stats['blocked'] + stats['failed']
        if processed > 0:
            progress_percent = (processed / total_recipients) * 100

            # Calculate ETA
            if elapsed > 0:
                items_per_second = processed / elapsed
                if items_per_second > 0:
                    remaining = total_recipients - processed
                    eta_seconds = remaining / items_per_second
                    eta_str = format_time(eta_seconds)
                else:
                    eta_str = "∞"
            else:
                eta_str = "Calculating..."

            # Create status text
            mode_emoji = "📋" if not use_forward else "🔄"
            status_text = (
                f"{mode_emoji} **Broadcast in Progress**\n\n"
                f"📊 **Progress:** {create_progress_bar(progress_percent)}\n"
                f"✅ **Sent:** {stats['sent']:,}/{total_recipients:,}\n"
                f"⛔ **Blocked:** {stats['blocked']:,}\n"
                f"❌ **Failed:** {stats['failed']:,}\n"
                f"🔄 **Retries:** {stats['retry_count']:,}\n"
                f"⏱️ **Elapsed:** {format_time(elapsed)}\n"
                f"⏳ **ETA:** {eta_str}"
            )

            try:
                await status_msg.edit_text(status_text)
                stats['last_update_time'] = current_time
                stats['last_update_count'] = processed
            except Exception as e:
                # Silently fail if we can't edit (message deleted, etc.)
                logger.debug(f"Failed to update status: {str(e)}")

    # Process recipients in batches to avoid memory issues
    recipients_list = list(all_recipients)
    total_sent_in_batch = 0
    BATCH_SIZE = 30  # Process 30 messages then take a break
    BATCH_DELAY = 1.0  # 1 second delay after each batch

    for index, chat_id in enumerate(recipients_list, 1):
        # Check if broadcast was cancelled
        if broadcast_running['cancel']:
            logger.info("⚠️ Broadcast cancelled by user")
            await status_msg.edit_text(
                "🛑 **Broadcast Cancelled**\n\n"
                f"Stopped at {index}/{total_recipients} recipients"
            )
            broadcast_running['status'] = False
            return

        stats['current_index'] = index

        # Update status every 15 recipients or every 3 seconds
        processed = stats['sent'] + stats['blocked'] + stats['failed']
        if (processed - stats['last_update_count'] >= 15 or 
            time.time() - stats['last_update_time'] >= 3):
            await update_status()

        # Try to send message with retry logic
        max_retries = 2
        retry_count = 0
        message_sent = False

        while retry_count <= max_retries and not message_sent:
            try:
                if use_forward:
                    # Forward message (with forward tag)
                    await context.bot.forward_message(
                        chat_id=chat_id,
                        from_chat_id=message_to_broadcast.chat_id,
                        message_id=message_to_broadcast.message_id
                    )
                else:
                    # Copy message (without forward tag)
                    await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=message_to_broadcast.chat_id,
                        message_id=message_to_broadcast.message_id
                    )
                
                stats['sent'] += 1
                
                # Track if it's a group or user
                if chat_id in all_chats:
                    stats['groups_sent'] += 1
                elif chat_id in all_users:
                    stats['users_sent'] += 1
                
                message_sent = True
                logger.debug(f"✅ Sent to {chat_id}")

            except RetryAfter as e:
                # FloodWait - sleep and retry
                if retry_count < max_retries:
                    wait_time = min(e.retry_after, 30)  # Max 30 seconds wait
                    logger.warning(f"⏳ Rate limited. Waiting {wait_time}s")
                    
                    await status_msg.edit_text(
                        f"⏳ **Rate Limited**\n"
                        f"Waiting {wait_time} seconds before continuing...\n\n"
                        f"Progress: {stats['sent']:,}/{total_recipients:,}"
                    )
                    await asyncio.sleep(wait_time)
                    retry_count += 1
                    stats['retry_count'] += 1
                else:
                    stats['failed'] += 1
                    logger.error(f"❌ Failed after retries: {chat_id}")
                    message_sent = True  # Exit retry loop

            except Forbidden:
                # User blocked the bot or bot was removed from group
                stats['blocked'] += 1
                message_sent = True
                logger.debug(f"⛔ Blocked: {chat_id}")

            except BadRequest as e:
                # Deleted account or invalid chat ID
                error_msg = str(e).lower()
                if any(x in error_msg for x in ["chat not found", "user not found", "deactivated"]):
                    stats['failed'] += 1
                    logger.debug(f"❌ Invalid chat: {chat_id}")
                else:
                    stats['failed'] += 1
                    logger.error(f"❌ BadRequest for {chat_id}: {str(e)}")
                message_sent = True

            except TelegramError as e:
                # Other Telegram API errors
                stats['failed'] += 1
                logger.error(f"❌ TelegramError for {chat_id}: {str(e)}")
                message_sent = True

            except Exception as e:
                # Any other unexpected errors
                stats['failed'] += 1
                logger.exception(f"❌ Unexpected error for {chat_id}: {str(e)}")
                message_sent = True

        # Batch delay logic
        total_sent_in_batch += 1
        if total_sent_in_batch >= BATCH_SIZE:
            logger.info(f"📦 Batch complete ({BATCH_SIZE} messages). Taking {BATCH_DELAY}s break...")
            await asyncio.sleep(BATCH_DELAY)
            total_sent_in_batch = 0
        else:
            # Small delay between individual messages
            await asyncio.sleep(0.05)

    # Final summary with small caps
    summary = (
        f"✅ {to_small_caps('broadcast complete!')}\n\n"
        f"📊 {to_small_caps('total send')}: {stats['sent']:,}\n"
        f"👥 {to_small_caps('groups send')}: {stats['groups_sent']:,}\n"
        f"💬 {to_small_caps('users dm send')}: {stats['users_sent']:,}\n"
        f"⛔ {to_small_caps('blocked')}: {stats['blocked']:,}\n"
        f"❌ {to_small_caps('failed')}: {stats['failed']:,}"
    )

    logger.info(f"🎉 Broadcast completed: {stats['sent']}/{total_recipients} sent")

    try:
        await status_msg.edit_text(summary)
    except:
        # If status message was deleted, send new one
        await update.message.reply_text(summary)

    # Reset broadcast running flag
    broadcast_running['status'] = False


async def cancel_broadcast(update: Update, context: CallbackContext) -> None:
    """Cancel the running broadcast (Owner only)."""
    
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied. Owner only.")
        return

    if not broadcast_running['status']:
        await update.message.reply_text("❌ No broadcast is currently running.")
        return

    broadcast_running['cancel'] = True
    await update.message.reply_text("🛑 Cancelling broadcast... Please wait.")
    logger.info("🛑 Broadcast cancel requested")


# Add handlers to application
application.add_handler(CommandHandler("broadcast", broadcast, block=False))
application.add_handler(CommandHandler("cancelbc", cancel_broadcast, block=False))
