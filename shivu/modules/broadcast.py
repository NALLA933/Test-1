import asyncio
import time
from typing import AsyncGenerator, Dict, List, Set
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import (
    BadRequest, 
    Forbidden, 
    RetryAfter, 
    ChatMigrated,
    TelegramError
)
from shivu import application, top_global_groups_collection, pm_users

# ============================================================================
#                           CONFIGURATION
# ============================================================================

# Authorization
OWNER_ID = 8453236527
SUDO_USERS = [8420981179, 7818323042]
AUTHORIZED_USERS = {OWNER_ID, *SUDO_USERS}

# Broadcast Settings
MAX_CONCURRENT_TASKS = 20  # Reduced for better stability
BATCH_SIZE = 50  # Batch size for progress updates
MAX_RETRIES = 1  # Retry once for flood waits
UPDATE_INTERVAL = 5  # Update status every N seconds

# ============================================================================
#                         SMALL CAPS CONVERTER
# ============================================================================

SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ', 
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
}

def to_small_caps(text: str) -> str:
    """Convert text to small caps Unicode"""
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

# ============================================================================
#                      ASYNC GENERATOR FOR MEMORY EFFICIENCY
# ============================================================================

async def fetch_chat_ids_generator() -> AsyncGenerator[int, None]:
    """
    Fetch chat IDs one by one using async generator to minimize memory usage.
    Handles both users and groups with proper ID formatting.
    """
    seen_ids: Set[int] = set()
    
    # Fetch PM Users
    async for user_doc in pm_users.find({}, {"_id": 1}):
        user_id = user_doc.get("_id")
        if user_id and user_id not in seen_ids:
            seen_ids.add(user_id)
            yield user_id
    
    # Fetch Groups with ID formatting
    async for group_doc in top_global_groups_collection.find({}, {"group_id": 1}):
        group_id = group_doc.get("group_id")
        if not group_id:
            continue
        
        # Convert to proper Telegram supergroup format
        formatted_id = format_group_id(group_id)
        
        if formatted_id and formatted_id not in seen_ids:
            seen_ids.add(formatted_id)
            yield formatted_id

def format_group_id(group_id) -> int:
    """
    Format group ID to Telegram supergroup format.
    Handles both int and str types.
    
    Rules:
    - If positive int: prepend -100 (e.g., 123456789 -> -100123456789)
    - If already negative: return as is
    - If string: convert to int first, then apply rules
    """
    try:
        # Convert to int if string
        if isinstance(group_id, str):
            group_id = int(group_id)
        
        # If positive, add -100 prefix
        if group_id > 0:
            return int(f"-100{group_id}")
        
        # Already negative (likely correct format)
        return group_id
    except (ValueError, TypeError):
        return None

# ============================================================================
#                         BROADCAST STATISTICS
# ============================================================================

class BroadcastStats:
    """Track broadcast statistics"""
    def __init__(self):
        self.success = 0
        self.blocked = 0
        self.chat_not_found = 0
        self.flood_wait = 0
        self.chat_migrated = 0
        self.other_errors = 0
        self.total_processed = 0
        self.invalid_ids: List[int] = []
        
    @property
    def total_failed(self) -> int:
        return self.blocked + self.chat_not_found + self.other_errors
    
    @property
    def success_rate(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return (self.success / self.total_processed) * 100

# ============================================================================
#                         MESSAGE SENDER WITH RETRY
# ============================================================================

async def send_to_chat(
    context: CallbackContext,
    chat_id: int,
    message_id: int,
    source_chat_id: int,
    stats: BroadcastStats,
    semaphore: asyncio.Semaphore
) -> None:
    """
    Send message to a single chat with error handling and retry logic.
    """
    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat_id,
                    message_id=message_id,
                    disable_notification=True
                )
                stats.success += 1
                return
                
            except RetryAfter as e:
                # Flood control - wait and retry once
                stats.flood_wait += 1
                if attempt < MAX_RETRIES:
                    wait_time = min(e.retry_after, 60)  # Cap at 60 seconds
                    await asyncio.sleep(wait_time)
                    continue
                return
                
            except Forbidden:
                # Bot was blocked by user
                stats.blocked += 1
                stats.invalid_ids.append(chat_id)
                return
                
            except BadRequest as e:
                error_msg = str(e).lower()
                
                if "chat not found" in error_msg or "peer_id_invalid" in error_msg:
                    stats.chat_not_found += 1
                    stats.invalid_ids.append(chat_id)
                elif "user is deactivated" in error_msg:
                    stats.blocked += 1
                    stats.invalid_ids.append(chat_id)
                else:
                    stats.other_errors += 1
                return
                
            except ChatMigrated as e:
                # Group upgraded to supergroup
                stats.chat_migrated += 1
                new_chat_id = e.new_chat_id
                
                # Try sending to new chat ID (no retry for this)
                try:
                    await context.bot.copy_message(
                        chat_id=new_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=message_id,
                        disable_notification=True
                    )
                    stats.success += 1
                except Exception:
                    stats.other_errors += 1
                return
                
            except TelegramError:
                # Generic Telegram error
                stats.other_errors += 1
                return
                
            except Exception:
                # Unexpected error
                stats.other_errors += 1
                return

# ============================================================================
#                         STATUS MESSAGE GENERATORS
# ============================================================================

def generate_live_stats(
    stats: BroadcastStats,
    elapsed_time: float,
    is_final: bool = False
) -> str:
    """Generate live statistics message"""
    
    speed = stats.success / max(1, elapsed_time)
    line = "━" * 30
    
    if is_final:
        header = to_small_caps("✨ BROADCAST COMPLETED")
    else:
        header = to_small_caps("📤 BROADCASTING IN PROGRESS")
    
    return f"""<b>{header}</b>
<code>{line}</code>
<b>📊 {to_small_caps("STATISTICS")}</b>

✅ <b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ:</b> <code>{stats.success:,}</code>
🚫 <b>ʙʟᴏᴄᴋᴇᴅ:</b> <code>{stats.blocked:,}</code>
❌ <b>ᴄʜᴀᴛ ɴᴏᴛ ꜰᴏᴜɴᴅ:</b> <code>{stats.chat_not_found:,}</code>
⏳ <b>ꜰʟᴏᴏᴅ ᴡᴀɪᴛ:</b> <code>{stats.flood_wait:,}</code>
🔄 <b>ᴍɪɢʀᴀᴛᴇᴅ:</b> <code>{stats.chat_migrated:,}</code>
⚠️ <b>ᴏᴛʜᴇʀ ᴇʀʀᴏʀꜱ:</b> <code>{stats.other_errors:,}</code>

<code>{line}</code>
<b>📈 {to_small_caps("PERFORMANCE")}</b>

👥 <b>ᴘʀᴏᴄᴇꜱꜱᴇᴅ:</b> <code>{stats.total_processed:,}</code>
📊 <b>ꜱᴜᴄᴄᴇꜱꜱ ʀᴀᴛᴇ:</b> <code>{stats.success_rate:.1f}%</code>
⚡ <b>ꜱᴘᴇᴇᴅ:</b> <code>{speed:.1f} msg/sec</code>
⏱️ <b>ᴛɪᴍᴇ:</b> <code>{elapsed_time:.1f}s</code>
<code>{line}</code>"""

def generate_cleanup_summary(stats: BroadcastStats) -> str:
    """Generate database cleanup summary"""
    
    if not stats.invalid_ids:
        return None
    
    line = "━" * 30
    
    # Separate users from groups
    users = [id for id in stats.invalid_ids if id > 0]
    groups = [id for id in stats.invalid_ids if id < 0]
    
    summary = f"""<b>🧹 {to_small_caps("DATABASE CLEANUP REQUIRED")}</b>
<code>{line}</code>

<b>📋 {to_small_caps("INVALID ENTRIES FOUND")}</b>

🚫 <b>ʙʟᴏᴄᴋᴇᴅ/ɪɴᴠᴀʟɪᴅ ᴜꜱᴇʀꜱ:</b> <code>{len(users):,}</code>
🚫 <b>ɪɴᴠᴀʟɪᴅ ɢʀᴏᴜᴘꜱ:</b> <code>{len(groups):,}</code>

<code>{line}</code>
<b>💡 {to_small_caps("CLEANUP SUGGESTIONS")}</b>

<i>Remove invalid users:</i>
<code>await pm_users.delete_many({{'_id': {{'$in': [list of user IDs]}}})</code>

<i>Remove invalid groups:</i>
<code>await top_global_groups_collection.delete_many({{'group_id': {{'$in': [list of group IDs]}}})</code>

<b>⚠️ Total entries to clean:</b> <code>{len(stats.invalid_ids):,}</code>"""
    
    return summary

# ============================================================================
#                         MAIN BROADCAST HANDLER
# ============================================================================

# Global broadcast lock
broadcast_lock = asyncio.Lock()

async def broadcast_command(update: Update, context: CallbackContext) -> None:
    """Main broadcast command handler"""
    
    # Authorization check
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        await update.message.reply_text(
            f"<b>❌ {to_small_caps('ACCESS DENIED')}</b>\n"
            f"<i>You are not authorized to use this command.</i>",
            parse_mode='HTML'
        )
        return
    
    # Check if broadcast is already running
    if broadcast_lock.locked():
        await update.message.reply_text(
            f"<b>⏳ {to_small_caps('BROADCAST IN PROGRESS')}</b>\n"
            f"<i>Please wait for the current broadcast to complete.</i>",
            parse_mode='HTML'
        )
        return
    
    # Check for replied message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            f"<b>📝 {to_small_caps('REPLY REQUIRED')}</b>\n"
            f"<i>Please reply to a message to broadcast it.</i>",
            parse_mode='HTML'
        )
        return
    
    async with broadcast_lock:
        stats = BroadcastStats()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        
        message_to_broadcast = update.message.reply_to_message
        source_chat_id = message_to_broadcast.chat_id
        message_id = message_to_broadcast.message_id
        
        # Initial status message
        status_msg = await update.message.reply_text(
            f"<b>🚀 {to_small_caps('STARTING BROADCAST')}</b>\n"
            f"<i>Preparing to send messages...</i>",
            parse_mode='HTML'
        )
        
        start_time = time.time()
        last_update_time = start_time
        
        # Create tasks queue
        tasks = []
        batch_counter = 0
        
        try:
            # Process chats using async generator (memory efficient)
            async for chat_id in fetch_chat_ids_generator():
                stats.total_processed += 1
                
                # Create send task
                task = asyncio.create_task(
                    send_to_chat(
                        context=context,
                        chat_id=chat_id,
                        message_id=message_id,
                        source_chat_id=source_chat_id,
                        stats=stats,
                        semaphore=semaphore
                    )
                )
                tasks.append(task)
                
                # Update status periodically
                current_time = time.time()
                if current_time - last_update_time >= UPDATE_INTERVAL:
                    elapsed = current_time - start_time
                    try:
                        await status_msg.edit_text(
                            generate_live_stats(stats, elapsed, False),
                            parse_mode='HTML'
                        )
                    except Exception:
                        pass  # Ignore edit errors
                    last_update_time = current_time
                
                # Process in batches to avoid memory buildup
                batch_counter += 1
                if batch_counter >= BATCH_SIZE:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
                    batch_counter = 0
            
            # Wait for remaining tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Final statistics
            elapsed_total = time.time() - start_time
            
            # Send final report
            await status_msg.edit_text(
                generate_live_stats(stats, elapsed_total, True),
                parse_mode='HTML'
            )
            
            # Send cleanup summary if needed
            cleanup_msg = generate_cleanup_summary(stats)
            if cleanup_msg:
                await update.message.reply_text(
                    cleanup_msg,
                    parse_mode='HTML'
                )
            
            # Send completion notification
            await update.message.reply_text(
                f"<b>✅ {to_small_caps('BROADCAST COMPLETED')}</b>\n"
                f"<i>Successfully delivered to <b>{stats.success:,}</b> chats!</i>\n"
                f"⏱️ <b>ᴛᴏᴛᴀʟ ᴛɪᴍᴇ:</b> <code>{elapsed_total:.1f}s</code>",
                parse_mode='HTML'
            )
            
        except Exception as e:
            await status_msg.edit_text(
                f"<b>❌ {to_small_caps('BROADCAST ERROR')}</b>\n"
                f"<code>{str(e)}</code>",
                parse_mode='HTML'
            )

# ============================================================================
#                         REGISTER HANDLER
# ============================================================================

application.add_handler(CommandHandler("broadcast", broadcast_command, block=False))

print("✅ Premium Broadcast System loaded successfully!")
