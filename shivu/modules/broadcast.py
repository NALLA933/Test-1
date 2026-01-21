import asyncio
import time
from typing import AsyncGenerator, List, Set
from telegram import Update
from telegram.ext import CallbackContext, CommandHandler
from telegram.error import BadRequest, Forbidden, RetryAfter, ChatMigrated, TelegramError
from shivu import application, top_global_groups_collection, pm_users

# ============================================================================
#                           CONFIGURATION
# ============================================================================

OWNER_ID = 8453236527
MAX_CONCURRENT_TASKS = 25
BATCH_SIZE = 50
MAX_RETRIES = 1
UPDATE_INTERVAL = 5

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
    return ''.join(SMALL_CAPS_MAP.get(c, c) for c in text)

# ============================================================================
#                      ASYNC GENERATOR FOR MEMORY EFFICIENCY
# ============================================================================

async def fetch_chat_ids_generator() -> AsyncGenerator[int, None]:
    seen_ids: Set[int] = set()
    
    async for user_doc in pm_users.find({}, {"_id": 1}):
        user_id = user_doc.get("_id")
        if user_id and user_id not in seen_ids:
            seen_ids.add(user_id)
            yield user_id
    
    async for group_doc in top_global_groups_collection.find({}, {"group_id": 1}):
        group_id = group_doc.get("group_id")
        if not group_id:
            continue
        
        formatted_id = format_group_id(group_id)
        if formatted_id and formatted_id not in seen_ids:
            seen_ids.add(formatted_id)
            yield formatted_id

def format_group_id(group_id) -> int:
    try:
        if isinstance(group_id, str):
            group_id = int(group_id)
        
        if group_id > 0:
            return int(f"-100{group_id}")
        
        return group_id
    except (ValueError, TypeError):
        return None

# ============================================================================
#                         BROADCAST STATISTICS
# ============================================================================

class BroadcastStats:
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
                stats.flood_wait += 1
                if attempt < MAX_RETRIES:
                    wait_time = min(e.retry_after, 60)
                    await asyncio.sleep(wait_time)
                    continue
                return
                
            except Forbidden:
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
                stats.chat_migrated += 1
                new_chat_id = e.new_chat_id
                
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
                stats.other_errors += 1
                return
                
            except Exception:
                stats.other_errors += 1
                return

# ============================================================================
#                         STATUS MESSAGE GENERATORS
# ============================================================================

def generate_live_stats(stats: BroadcastStats, elapsed_time: float, is_final: bool = False) -> str:
    speed = stats.success / max(1, elapsed_time)
    line = "━" * 32
    
    header = to_small_caps("✨ BROADCAST COMPLETED") if is_final else to_small_caps("📤 BROADCASTING")
    
    return f"""<b>{header}</b>
<code>{line}</code>
<b>📊 {to_small_caps("STATISTICS")}</b>

✅ <b>ꜱᴇɴᴛ:</b> <code>{stats.success:,}</code>
🚫 <b>ʙʟᴏᴄᴋᴇᴅ:</b> <code>{stats.blocked:,}</code>
❌ <b>ɴᴏᴛ ꜰᴏᴜɴᴅ:</b> <code>{stats.chat_not_found:,}</code>
⏳ <b>ꜰʟᴏᴏᴅ:</b> <code>{stats.flood_wait:,}</code>
🔄 <b>ᴍɪɢʀᴀᴛᴇᴅ:</b> <code>{stats.chat_migrated:,}</code>
⚠️ <b>ᴇʀʀᴏʀꜱ:</b> <code>{stats.other_errors:,}</code>

<code>{line}</code>
<b>📈 {to_small_caps("PERFORMANCE")}</b>

👥 <b>ᴘʀᴏᴄᴇꜱꜱᴇᴅ:</b> <code>{stats.total_processed:,}</code>
📊 <b>ꜱᴜᴄᴄᴇꜱꜱ:</b> <code>{stats.success_rate:.1f}%</code>
⚡ <b>ꜱᴘᴇᴇᴅ:</b> <code>{speed:.1f} msg/s</code>
⏱️ <b>ᴛɪᴍᴇ:</b> <code>{elapsed_time:.1f}s</code>
<code>{line}</code>"""

def generate_cleanup_summary(stats: BroadcastStats) -> str:
    if not stats.invalid_ids:
        return None
    
    line = "━" * 32
    users = [id for id in stats.invalid_ids if id > 0]
    groups = [id for id in stats.invalid_ids if id < 0]
    
    return f"""<b>🧹 {to_small_caps("CLEANUP REQUIRED")}</b>
<code>{line}</code>

<b>📋 {to_small_caps("INVALID ENTRIES")}</b>

🚫 <b>ɪɴᴠᴀʟɪᴅ ᴜꜱᴇʀꜱ:</b> <code>{len(users):,}</code>
🚫 <b>ɪɴᴠᴀʟɪᴅ ɢʀᴏᴜᴘꜱ:</b> <code>{len(groups):,}</code>

<code>{line}</code>
<b>💡 {to_small_caps("CLEANUP CODE")}</b>

<i>Remove invalid users:</i>
<code>await pm_users.delete_many({{'_id': {{'$in': [...]}}}})</code>

<i>Remove invalid groups:</i>
<code>await top_global_groups_collection.delete_many({{'group_id': {{'$in': [...]}}}})</code>

<b>⚠️ Total invalid:</b> <code>{len(stats.invalid_ids):,}</code>"""

# ============================================================================
#                         MAIN BROADCAST HANDLER
# ============================================================================

broadcast_lock = asyncio.Lock()

async def broadcast_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await update.message.reply_text(
            f"<b>❌ {to_small_caps('ACCESS DENIED')}</b>\n"
            f"<i>Owner-only command.</i>",
            parse_mode='HTML'
        )
        return
    
    if broadcast_lock.locked():
        await update.message.reply_text(
            f"<b>⏳ {to_small_caps('BROADCAST RUNNING')}</b>\n"
            f"<i>Please wait for completion.</i>",
            parse_mode='HTML'
        )
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            f"<b>📝 {to_small_caps('REPLY REQUIRED')}</b>\n"
            f"<i>Reply to a message to broadcast.</i>",
            parse_mode='HTML'
        )
        return
    
    async with broadcast_lock:
        stats = BroadcastStats()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        
        message_to_broadcast = update.message.reply_to_message
        source_chat_id = message_to_broadcast.chat_id
        message_id = message_to_broadcast.message_id
        
        status_msg = await update.message.reply_text(
            f"<b>🚀 {to_small_caps('INITIALIZING')}</b>\n"
            f"<i>Preparing broadcast...</i>",
            parse_mode='HTML'
        )
        
        start_time = time.time()
        last_update_time = start_time
        
        tasks = []
        batch_counter = 0
        
        try:
            async for chat_id in fetch_chat_ids_generator():
                stats.total_processed += 1
                
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
                
                current_time = time.time()
                if current_time - last_update_time >= UPDATE_INTERVAL:
                    elapsed = current_time - start_time
                    try:
                        await status_msg.edit_text(
                            generate_live_stats(stats, elapsed, False),
                            parse_mode='HTML'
                        )
                    except Exception:
                        pass
                    last_update_time = current_time
                
                batch_counter += 1
                if batch_counter >= BATCH_SIZE:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
                    batch_counter = 0
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            elapsed_total = time.time() - start_time
            
            await status_msg.edit_text(
                generate_live_stats(stats, elapsed_total, True),
                parse_mode='HTML'
            )
            
            cleanup_msg = generate_cleanup_summary(stats)
            if cleanup_msg:
                await update.message.reply_text(cleanup_msg, parse_mode='HTML')
            
            await update.message.reply_text(
                f"<b>✅ {to_small_caps('COMPLETED')}</b>\n"
                f"<i>Delivered to <b>{stats.success:,}</b> chats</i>\n"
                f"⏱️ <code>{elapsed_total:.1f}s</code>",
                parse_mode='HTML'
            )
            
        except Exception as e:
            await status_msg.edit_text(
                f"<b>❌ {to_small_caps('ERROR')}</b>\n<code>{str(e)}</code>",
                parse_mode='HTML'
            )

# ============================================================================
#                         REGISTER HANDLER
# ============================================================================

application.add_handler(CommandHandler("broadcast", broadcast_command, block=False))
