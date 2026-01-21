import asyncio
import time
from typing import AsyncGenerator, Dict, List, Set, Tuple
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import (
    BadRequest, 
    Forbidden, 
    RetryAfter, 
    ChatMigrated,
    TelegramError
)
from shivu import application, top_global_groups_collection, user_collection, pm_users

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
#                       GROUP ID FORMATTING (FIXED)
# ============================================================================

def format_group_id(group_id) -> int:
    """
    Format group ID to Telegram supergroup format.
    Handles both int and str types.
    
    Rules:
    - If positive int: prepend -100 (e.g., 123456 -> -100123456)
    - If already negative: return as is
    - If string: convert to int first, then apply rules
    
    Returns: Formatted int or None if invalid
    """
    try:
        # Convert to int if string
        if isinstance(group_id, str):
            # Handle string that might already have -100 prefix
            if group_id.startswith('-100'):
                return int(group_id)
            group_id = int(group_id)
        
        # If positive, add -100 prefix
        if group_id > 0:
            # Check if it's already a 12+ digit ID (might already be supergroup)
            if group_id > 1000000000000:
                # It's already a supergroup ID, keep as negative
                return -group_id
            return int(f"-100{group_id}")
        
        # Already negative (likely correct format)
        return group_id
        
    except (ValueError, TypeError) as e:
        print(f"⚠️ Invalid group ID format: {group_id}, Error: {e}")
        return None

# ============================================================================
#                 PRIORITIZED CHAT FETCHER (GROUPS FIRST)
# ============================================================================

async def fetch_chat_ids_generator() -> AsyncGenerator[Tuple[int, str], None]:
    """
    Fetch chat IDs one by one with GROUPS FIRST, then Users.
    Returns tuple of (chat_id, type) where type is 'group' or 'user'
    """
    seen_ids: Set[int] = set()
    
    # PHASE 1: PROCESS GROUPS FIRST (CRITICAL FIX)
    async for group_doc in top_global_groups_collection.find({}, {"group_id": 1}):
        group_id = group_doc.get("group_id")
        if not group_id:
            continue
        
        # Format group ID properly
        formatted_id = format_group_id(group_id)
        
        if formatted_id and formatted_id not in seen_ids:
            seen_ids.add(formatted_id)
            yield (formatted_id, 'group')
    
    # PHASE 2: PROCESS USERS (Private Messages)
    # Try pm_users first, fallback to user_collection
    user_collections_to_try = [pm_users, user_collection] if pm_users != user_collection else [pm_users]
    
    for user_coll in user_collections_to_try:
        try:
            async for user_doc in user_coll.find({}, {"_id": 1}):
                user_id = user_doc.get("_id")
                if user_id and user_id not in seen_ids:
                    seen_ids.add(user_id)
                    yield (user_id, 'user')
        except Exception as e:
            print(f"⚠️ Error reading from user collection: {e}")
            continue

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
        self.failed_groups: List[int] = []  # Track failed groups separately
        self.failed_users: List[int] = []   # Track failed users separately
        
    @property
    def total_failed(self) -> int:
        return self.blocked + self.chat_not_found + self.other_errors
    
    @property
    def success_rate(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return (self.success / self.total_processed) * 100

# ============================================================================
#                 ENHANCED MESSAGE SENDER WITH GROUP DEBUGGING
# ============================================================================

async def send_to_chat(
    context: CallbackContext,
    chat_id: int,
    chat_type: str,
    message_id: int,
    source_chat_id: int,
    stats: BroadcastStats,
    semaphore: asyncio.Semaphore
) -> None:
    """
    Send message to a single chat with error handling and retry logic.
    Special debugging for groups.
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
                if chat_type == 'group':
                    print(f"✅ Successfully sent to Group: {chat_id}")
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
                # Bot was blocked or doesn't have permission
                stats.blocked += 1
                if chat_type == 'group':
                    print(f"🚫 Failed Group {chat_id}: Bot blocked or no permissions")
                    stats.failed_groups.append(chat_id)
                else:
                    stats.failed_users.append(chat_id)
                stats.invalid_ids.append(chat_id)
                return
                
            except BadRequest as e:
                error_msg = str(e).lower()
                
                if "chat not found" in error_msg or "peer_id_invalid" in error_msg:
                    stats.chat_not_found += 1
                    if chat_type == 'group':
                        print(f"❌ Failed Group {chat_id}: Chat not found")
                        stats.failed_groups.append(chat_id)
                    else:
                        stats.failed_users.append(chat_id)
                    stats.invalid_ids.append(chat_id)
                elif "user is deactivated" in error_msg:
                    stats.blocked += 1
                    if chat_type == 'group':
                        print(f"🚫 Failed Group {chat_id}: Group deactivated")
                        stats.failed_groups.append(chat_id)
                    else:
                        stats.failed_users.append(chat_id)
                    stats.invalid_ids.append(chat_id)
                else:
                    stats.other_errors += 1
                    if chat_type == 'group':
                        print(f"⚠️ Failed Group {chat_id}: {error_msg}")
                        stats.failed_groups.append(chat_id)
                return
                
            except ChatMigrated as e:
                # Group upgraded to supergroup
                stats.chat_migrated += 1
                new_chat_id = e.new_chat_id
                
                if chat_type == 'group':
                    print(f"🔄 Group migrated: {chat_id} -> {new_chat_id}")
                
                # Try sending to new chat ID (no retry for this)
                try:
                    await context.bot.copy_message(
                        chat_id=new_chat_id,
                        from_chat_id=source_chat_id,
                        message_id=message_id,
                        disable_notification=True
                    )
                    stats.success += 1
                except Exception as send_error:
                    stats.other_errors += 1
                    if chat_type == 'group':
                        print(f"⚠️ Failed to send to migrated group {new_chat_id}: {send_error}")
                        stats.failed_groups.append(chat_id)
                return
                
            except TelegramError as e:
                # Generic Telegram error
                stats.other_errors += 1
                if chat_type == 'group':
                    print(f"⚠️ Telegram error for Group {chat_id}: {e}")
                    stats.failed_groups.append(chat_id)
                return
                
            except Exception as e:
                # Unexpected error
                stats.other_errors += 1
                if chat_type == 'group':
                    print(f"⚠️ Unexpected error for Group {chat_id}: {e}")
                    stats.failed_groups.append(chat_id)
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
    
    # Prepare group IDs for deletion (convert back to original format)
    group_ids_for_db = []
    for group_id in stats.failed_groups:
        # Convert back to positive ID for database
        if group_id < -1000000000000:
            # Remove -100 prefix
            original_id = abs(group_id) % 1000000000
            group_ids_for_db.append(original_id)
        elif group_id < 0:
            group_ids_for_db.append(abs(group_id))
    
    summary = f"""<b>🧹 {to_small_caps("DATABASE CLEANUP REQUIRED")}</b>
<code>{line}</code>

<b>📋 {to_small_caps("INVALID ENTRIES FOUND")}</b>

🚫 <b>ɪɴᴠᴀʟɪᴅ ᴜꜱᴇʀꜱ:</b> <code>{len(stats.failed_users):,}</code>
🚫 <b>ɪɴᴠᴀʟɪᴅ ɢʀᴏᴜᴘꜱ:</b> <code>{len(stats.failed_groups):,}</code>

<code>{line}</code>
<b>💡 {to_small_caps("CLEANUP SUGGESTIONS")}</b>

<i>Remove invalid users:</i>
<code>await pm_users.delete_many({{'_id': {{'$in': {stats.failed_users[:10] if len(stats.failed_users) > 10 else stats.failed_users}}}}})</code>

<i>Remove invalid groups:</i>
<code>await top_global_groups_collection.delete_many({{'group_id': {{'$in': {group_ids_for_db[:10] if len(group_ids_for_db) > 10 else group_ids_for_db}}}}})</code>

<b>⚠️ Total entries to clean:</b> <code>{len(stats.invalid_ids):,}</code>"""
    
    return summary

# ============================================================================
#                    MAIN BROADCAST HANDLER (FIXED)
# ============================================================================

# Global broadcast lock
broadcast_lock = asyncio.Lock()

async def broadcast_command(update: Update, context: CallbackContext) -> None:
    """Main broadcast command handler - FIXED VERSION with Groups First"""
    
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
            f"<i>Processing GROUPS first, then Users...</i>",
            parse_mode='HTML'
        )
        
        start_time = time.time()
        last_update_time = start_time
        
        # Create tasks queue
        tasks = []
        batch_counter = 0
        group_count = 0
        user_count = 0
        
        try:
            # Process chats using async generator (GROUPS FIRST)
            async for chat_id, chat_type in fetch_chat_ids_generator():
                stats.total_processed += 1
                
                # Track counts
                if chat_type == 'group':
                    group_count += 1
                else:
                    user_count += 1
                
                # Create send task
                task = asyncio.create_task(
                    send_to_chat(
                        context=context,
                        chat_id=chat_id,
                        chat_type=chat_type,
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
                            f"<b>📤 {to_small_caps('BROADCASTING IN PROGRESS')}</b>\n"
                            f"<code>━</code>\n"
                            f"<b>📊 {to_small_caps('STATISTICS')}</b>\n\n"
                            f"👥 <b>ᴛᴏᴛᴀʟ ᴘʀᴏᴄᴇꜱꜱᴇᴅ:</b> <code>{stats.total_processed:,}</code>\n"
                            f"👥 <b>ɢʀᴏᴜᴘꜱ:</b> <code>{group_count:,}</code>\n"
                            f"👤 <b>ᴜꜱᴇʀꜱ:</b> <code>{user_count:,}</code>\n"
                            f"✅ <b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ:</b> <code>{stats.success:,}</code>\n"
                            f"⏱️ <b>ᴛɪᴍᴇ:</b> <code>{elapsed:.1f}s</code>\n"
                            f"<code>━</code>\n"
                            f"<i>Groups are processed first. Console shows detailed group errors.</i>",
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
            
            # Send completion notification with group/user breakdown
            await update.message.reply_text(
                f"<b>✅ {to_small_caps('BROADCAST COMPLETED')}</b>\n\n"
                f"📊 <b>{to_small_caps('SUMMARY')}</b>\n"
                f"<code>━</code>\n"
                f"👥 <b>ᴛᴏᴛᴀʟ ɢʀᴏᴜᴘꜱ:</b> <code>{group_count:,}</code>\n"
                f"👤 <b>ᴛᴏᴛᴀʟ ᴜꜱᴇʀꜱ:</b> <code>{user_count:,}</code>\n"
                f"✅ <b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ ꜱᴇɴᴅꜱ:</b> <code>{stats.success:,}</code>\n"
                f"📈 <b>ꜱᴜᴄᴄᴇꜱꜱ ʀᴀᴛᴇ:</b> <code>{stats.success_rate:.1f}%</code>\n\n"
                f"⏱️ <b>ᴛᴏᴛᴀʟ ᴛɪᴍᴇ:</b> <code>{elapsed_total:.1f}s</code>\n"
                f"<code>━</code>\n"
                f"<i>Check console for detailed group sending errors.</i>",
                parse_mode='HTML'
            )
            
            # Print final debug info to console
            print(f"\n{'='*50}")
            print(f"📊 BROADCAST COMPLETED")
            print(f"{'='*50}")
            print(f"Total Processed: {stats.total_processed:,}")
            print(f"Groups: {group_count:,}")
            print(f"Users: {user_count:,}")
            print(f"Successful: {stats.success:,}")
            print(f"Failed Groups: {len(stats.failed_groups):,}")
            print(f"Failed Users: {len(stats.failed_users):,}")
            print(f"Success Rate: {stats.success_rate:.1f}%")
            print(f"Total Time: {elapsed_total:.1f}s")
            print(f"{'='*50}")
            
        except Exception as e:
            await status_msg.edit_text(
                f"<b>❌ {to_small_caps('BROADCAST ERROR')}</b>\n"
                f"<code>{str(e)}</code>",
                parse_mode='HTML'
            )
            print(f"⚠️ Broadcast error: {e}")

# ============================================================================
#                         REGISTER HANDLER
# ============================================================================

application.add_handler(CommandHandler("broadcast", broadcast_command, block=False))

print("✅ Premium Broadcast System loaded successfully!")
print("⚠️  IMPORTANT: Groups are processed FIRST, then Users")
print("⚠️  Console will show detailed group sending errors for debugging")
