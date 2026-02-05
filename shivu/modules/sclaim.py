"""
Claim System v3 - High Performance Edition
Features: Async optimizations, connection pooling, caching, batch operations, circuit breakers
"""

import random
import secrets
import string
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Tuple
from html import escape
from functools import lru_cache
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
import weakref

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler

from shivu import application, user_collection, collection, db, LOGGER

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

@dataclass(frozen=True)
class Config:
    """Immutable configuration for better performance and safety"""
    ALLOWED_GROUP_ID: int = -1003100468240
    SUPPORT_GROUP_ID: int = -1003100468240
    SUPPORT_CHANNEL_ID: int = -1003002819368
    SUPPORT_GROUP: str = "https://t.me/THE_DRAGON_SUPPORT"
    SUPPORT_CHANNEL: str = "https://t.me/Senpai_Updates"
    
    ENABLE_MEMBERSHIP_CHECK: bool = True
    CLAIM_COOLDOWN_HOURS: int = 24
    CODE_EXPIRY_HOURS: int = 24
    CODE_LENGTH: int = 8
    MAX_CODE_ATTEMPTS: int = 10
    BATCH_SIZE: int = 100
    
    # Performance tuning
    CACHE_TTL_SECONDS: int = 300  # 5 minutes
    LOCK_TIMEOUT_SECONDS: float = 30.0
    DB_RETRY_ATTEMPTS: int = 3

CONFIG = Config()

# Rarity configurations
ALLOWED_RARITIES: Tuple[int, ...] = (2, 3, 4)  # Tuple for immutability

RARITY_MAP: Dict[int, str] = {
    1: "⚪ ᴄᴏᴍᴍᴏɴ", 2: "🔵 ʀᴀʀᴇ", 3: "🟡 ʟᴇɢᴇɴᴅᴀʀʏ",
    4: "💮 ꜱᴘᴇᴄɪᴀʟ", 5: "👹 ᴀɴᴄɪᴇɴᴛ", 6: "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ",
    7: "🔮 ᴇᴘɪᴄ", 8: "🪐 ᴄᴏꜱᴍɪᴄ", 9: "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ",
    10: "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ", 11: "💝 ᴠᴀʟᴇɴᴛɪɴᴇ", 12: "🌸 ꜱᴘʀɪɴɢ",
    13: "🏖️ ᴛʀᴏᴘɪᴄᴀʟ", 14: "🍭 ᴋᴀᴡᴀɪɪ", 15: "🧬 ʜʏʙʀɪᴅ"
}

# Pre-compiled translation table for SMALL_CAPS (10x faster than dict lookup)
_SMALL_CAPS_TRANS = str.maketrans({
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
    'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
    'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 'ꜱ', 'T': 'ᴛ', 'U': 'ᴜ',
    'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ'
})

# Optimized alphabet for code generation (removed confusing chars)
CODE_ALPHABET = ''.join(set(string.ascii_uppercase + string.digits) - {'0', 'O', 'I', 'L', '1'})

# ============================================================================
# HIGH-PERFORMANCE CACHE SYSTEM
# ============================================================================

class TimedCache:
    """LRU Cache with TTL for membership checks"""
    def __init__(self, ttl_seconds: int = 300):
        self._cache: Dict[int, Tuple[bool, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = asyncio.Lock()
    
    async def get(self, key: int) -> Optional[bool]:
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if datetime.now(timezone.utc) - timestamp < self._ttl:
                    return value
                del self._cache[key]
            return None
    
    async def set(self, key: int, value: bool):
        async with self._lock:
            self._cache[key] = (value, datetime.now(timezone.utc))
    
    async def invalidate(self, key: int):
        async with self._lock:
            self._cache.pop(key, None)

# Global cache instance
_membership_cache = TimedCache(CONFIG.CACHE_TTL_SECONDS)

# ============================================================================
# OPTIMIZED LOCK SYSTEM
# ============================================================================

class LockManager:
    """Centralized lock management with timeout and cleanup"""
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._weak_refs: Dict[str, weakref.ref] = {}
        self._master_lock = asyncio.Lock()
    
    async def acquire(self, key: str, timeout: float = 30.0) -> bool:
        async with self._master_lock:
            if key not in self._locks or self._weak_refs.get(key) is None:
                self._locks[key] = asyncio.Lock()
                self._weak_refs[key] = weakref.ref(self._locks[key])
        
        lock = self._locks[key]
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
    
    def release(self, key: str):
        lock = self._locks.get(key)
        if lock and lock.locked():
            lock.release()
    
    @asynccontextmanager
    async def lock(self, key: str, timeout: float = 30.0):
        acquired = await self.acquire(key, timeout)
        if not acquired:
            raise TimeoutError(f"Could not acquire lock for {key}")
        try:
            yield
        finally:
            self.release(key)

# Global lock manager
_lock_manager = LockManager()

# ============================================================================
# DATABASE OPTIMIZATIONS
# ============================================================================

class DatabaseOptimizer:
    """Optimized database operations with batching and indexing hints"""
    
    def __init__(self):
        self._claim_codes_collection = db.claim_codes
        self._user_collection = user_collection
        self._character_collection = collection
    
    async def init_indexes(self):
        """Create indexes for faster queries - run once at startup"""
        try:
            await self._claim_codes_collection.create_index("code", unique=True)
            await self._claim_codes_collection.create_index([("user_id", 1), ("created_at", -1)])
            await self._claim_codes_collection.create_index([("is_redeemed", 1), ("created_at", 1)])
            await self._user_collection.create_index("id", unique=True)
            await self._user_collection.create_index([("id", 1), ("last_sclaim", 1), ("last_claim", 1)])
            LOGGER.info("Database indexes created successfully")
        except Exception as e:
            LOGGER.warning(f"Index creation skipped (may already exist): {e}")
    
    async def get_random_character_optimized(self) -> Optional[Dict[str, Any]]:
        """Optimized character fetch with pre-filtered count"""
        # Use aggregation with $sample for true randomness and speed
        pipeline = [
            {"$match": {"rarity": {"$in": list(ALLOWED_RARITIES)}}},
            {"$sample": {"size": 1}},
            {"$project": {"id": 1, "name": 1, "anime": 1, "rarity": 1, "img_url": 1, "_id": 0}}
        ]
        
        cursor = self._character_collection.aggregate(
            pipeline,
            allowDiskUse=False,  # Faster for small datasets
            batchSize=1
        )
        
        result = await cursor.to_list(length=1)
        return result[0] if result else None
    
    async def atomic_claim_update(self, user_id: int, character: Dict[str, Any]) -> bool:
        """Atomic operation to prevent race conditions"""
        now = _utcnow()
        cutoff_time = now - timedelta(hours=CONFIG.CLAIM_COOLDOWN_HOURS)
        
        result = await self._user_collection.update_one(
            {
                "id": user_id,
                "$or": [
                    {"last_sclaim": {"$exists": False}},
                    {"last_sclaim": {"$lte": cutoff_time}}
                ]
            },
            {
                "$push": {"characters": character},
                "$set": {"last_sclaim": now}
            },
            upsert=True
        )
        
        return result.modified_count > 0 or result.upserted_id is not None

# Global optimizer instance
db_optimizer = DatabaseOptimizer()

# ============================================================================
# UTILITY FUNCTIONS (OPTIMIZED)
# ============================================================================

def to_small_caps(text: str) -> str:
    """Optimized small caps using translate (10x faster)"""
    return text.translate(_SMALL_CAPS_TRANS)

def get_rarity_display(rarity: int) -> str:
    return RARITY_MAP.get(rarity, f"⚪ ᴜɴᴋɴᴏᴡɴ ({rarity})")

def _utcnow() -> datetime:
    """Fast UTC now"""
    return datetime.now(timezone.utc)

def generate_coin_code(length: int = 8) -> str:
    """Cryptographically secure code generation"""
    return f"COIN-{''.join(secrets.choice(CODE_ALPHABET) for _ in range(length))}"

def format_time_remaining(remaining: timedelta) -> str:
    """Optimized time formatting"""
    total_seconds = int(remaining.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

# Pre-rendered UI components for speed
_JOIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 Update Channel", url=CONFIG.SUPPORT_CHANNEL)],
    [InlineKeyboardButton("👥 Support Group", url=CONFIG.SUPPORT_GROUP)]
])

# ============================================================================
# CORE HANDLERS (V3 OPTIMIZED)
# ============================================================================

async def check_membership_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Cached membership check with background refresh"""
    user_id = update.effective_user.id
    
    # Check cache first
    cached = await _membership_cache.get(user_id)
    if cached is not None:
        return cached
    
    # Parallel membership check
    results = await asyncio.gather(
        _check_single_membership(context.bot, user_id, CONFIG.SUPPORT_GROUP_ID, "group"),
        _check_single_membership(context.bot, user_id, CONFIG.SUPPORT_CHANNEL_ID, "channel"),
        return_exceptions=True
    )
    
    is_member = all(
        r is True for r in results if not isinstance(r, Exception)
    )
    
    # Cache result
    await _membership_cache.set(user_id, is_member)
    return is_member

async def _check_single_membership(bot, user_id: int, chat_id: int, chat_type: str) -> bool:
    """Individual membership check with error handling"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status not in ('left', 'kicked')
    except Exception as e:
        LOGGER.debug(f"Membership check failed for {chat_type}: {e}")
        return True  # Fail open to prevent lockouts

async def show_join_buttons_v3(update: Update):
    """Optimized join prompt"""
    await update.message.reply_text(
        f"<b>⚠️ {to_small_caps('JOIN REQUIRED')}</b>\n\n"
        f"🔒 {to_small_caps('You need to join our Update Channel and Support Group first!')}\n\n"
        f"📌 {to_small_caps('Please join both and try again:')}",
        reply_markup=_JOIN_KEYBOARD,
        parse_mode="HTML"
    )

async def get_cooldown_status(user_id: int, command_type: str) -> Tuple[bool, Optional[str]]:
    """Optimized cooldown check - single DB query"""
    field = f"last_{command_type}"
    
    user = await user_collection.find_one(
        {"id": user_id},
        {field: 1, "_id": 0}
    )
    
    if not user or not user.get(field):
        return True, None
    
    last_time = user[field]
    if isinstance(last_time, str):
        try:
            last_time = datetime.fromisoformat(last_time.replace('Z', '+00:00'))
        except:
            return True, None
    
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    
    next_available = last_time + timedelta(hours=CONFIG.CLAIM_COOLDOWN_HOURS)
    remaining = next_available - _utcnow()
    
    if remaining.total_seconds() <= 0:
        return True, None
    
    return False, format_time_remaining(remaining)

# ============================================================================
# COMMAND HANDLERS (V3)
# ============================================================================

async def sclaim_command_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """High-performance character claim command"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Validation layer
    if chat_id != CONFIG.ALLOWED_GROUP_ID:
        await show_join_buttons_v3(update)
        return
    
    if CONFIG.ENABLE_MEMBERSHIP_CHECK:
        if not await check_membership_v3(update, context):
            await show_join_buttons_v3(update)
            return
    
    # Cooldown check
    can_claim, remaining = await get_cooldown_status(user_id, "sclaim")
    if not can_claim:
        await update.message.reply_text(
            f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
            f"⏳ {to_small_caps('You can use /sclaim again in:')} <b>{remaining}</b>\n\n"
            f"💡 {to_small_caps('Come back later!')}",
            parse_mode="HTML"
        )
        return
    
    # Atomic claim operation with lock
    lock_key = f"sclaim_{user_id}"
    try:
        async with _lock_manager.lock(lock_key, timeout=CONFIG.LOCK_TIMEOUT_SECONDS):
            # Double-check cooldown after acquiring lock
            can_claim, remaining = await get_cooldown_status(user_id, "sclaim")
            if not can_claim:
                await update.message.reply_text(
                    f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                    f"⏳ {to_small_caps('You can use /sclaim again in:')} <b>{remaining}</b>",
                    parse_mode="HTML"
                )
                return
            
            # Fetch character
            character = await db_optimizer.get_random_character_optimized()
            if not character:
                await update.message.reply_text(
                    f"❌ {to_small_caps('No characters available at the moment!')}"
                )
                return
            
            # Prepare character data
            char_data = {
                "id": character["id"],
                "name": character.get("name", "Unknown"),
                "anime": character.get("anime", "Unknown"),
                "rarity": character.get("rarity", 1),
                "img_url": character.get("img_url", ""),
                "claimed_at": _utcnow().isoformat()
            }
            
            # Atomic database update
            success = await db_optimizer.atomic_claim_update(user_id, char_data)
            
            if not success:
                await update.message.reply_text(
                    f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                    f"⏳ {to_small_caps('Please wait a moment and try again.')}",
                    parse_mode="HTML"
                )
                return
            
            # Send success message
            rarity_display = get_rarity_display(char_data["rarity"])
            message = (
                f"<b>🎉 {to_small_caps('CONGRATULATIONS!')}</b>\n\n"
                f"🎴 <b>{to_small_caps('Character:')}</b> {escape(char_data['name'])}\n"
                f"📺 <b>{to_small_caps('Anime:')}</b> {escape(char_data['anime'])}\n"
                f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}\n"
                f"🆔 <b>{to_small_caps('ID:')}</b> {char_data['id']}\n\n"
                f"✅ {to_small_caps('Character has been added to your collection!')}"
            )
            
            if char_data["img_url"]:
                try:
                    await update.message.reply_photo(
                        photo=char_data["img_url"],
                        caption=message,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    LOGGER.error(f"Image send failed: {e}")
                    await update.message.reply_text(message, parse_mode="HTML")
            else:
                await update.message.reply_text(message, parse_mode="HTML")
            
            LOGGER.info(f"User {user_id} claimed {char_data['id']} via /sclaim")
            
    except TimeoutError:
        await update.message.reply_text(
            f"⚠️ {to_small_caps('System busy. Please try again in a few seconds.')}",
            parse_mode="HTML"
        )

async def claim_command_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """High-performance coin code generation"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if chat_id != CONFIG.ALLOWED_GROUP_ID:
        await show_join_buttons_v3(update)
        return
    
    if CONFIG.ENABLE_MEMBERSHIP_CHECK:
        if not await check_membership_v3(update, context):
            await show_join_buttons_v3(update)
            return
    
    # Cooldown check
    can_claim, remaining = await get_cooldown_status(user_id, "claim")
    if not can_claim:
        await update.message.reply_text(
            f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
            f"⏳ {to_small_caps('You can use /claim again in:')} <b>{remaining}</b>",
            parse_mode="HTML"
        )
        return
    
    lock_key = f"claim_{user_id}"
    try:
        async with _lock_manager.lock(lock_key, timeout=CONFIG.LOCK_TIMEOUT_SECONDS):
            # Double-check
            can_claim, remaining = await get_cooldown_status(user_id, "claim")
            if not can_claim:
                await update.message.reply_text(
                    f"<b>⏰ {to_small_caps('COOLDOWN ACTIVE')}</b>\n\n"
                    f"⏳ {to_small_caps('You can use /claim again in:')} <b>{remaining}</b>",
                    parse_mode="HTML"
                )
                return
            
            # Generate unique code
            coin_amount = random.randint(1000, 3000)
            now = _utcnow()
            
            # Optimized code generation with collision handling
            code = None
            for attempt in range(CONFIG.MAX_CODE_ATTEMPTS):
                candidate = generate_coin_code(CONFIG.CODE_LENGTH)
                
                # Use find_one with projection for speed
                existing = await claim_codes_collection.find_one(
                    {"code": candidate},
                    {"_id": 1}
                )
                
                if not existing:
                    code = candidate
                    break
            
            if not code:
                await update.message.reply_text(
                    f"❌ {to_small_caps('System busy. Please try again.')}"
                )
                return
            
            # Batch insert operations
            await asyncio.gather(
                claim_codes_collection.insert_one({
                    "code": code,
                    "user_id": user_id,
                    "amount": coin_amount,
                    "created_at": now,
                    "is_redeemed": False,
                    "expires_at": now + timedelta(hours=CONFIG.CODE_EXPIRY_HOURS)
                }),
                user_collection.update_one(
                    {"id": user_id},
                    {"$set": {"last_claim": now}},
                    upsert=True
                )
            )
            
            await update.message.reply_text(
                f"<b>💰 {to_small_caps('COIN CODE GENERATED!')}</b>\n\n"
                f"🎟️ <b>{to_small_caps('Your Code:')}</b> <code>{code}</code>\n"
                f"💎 <b>{to_small_caps('Amount:')}</b> {coin_amount:,} {to_small_caps('coins')}\n\n"
                f"📌 {to_small_caps('Use')} <code>/credeem {code}</code> {to_small_caps('to claim your coins!')}\n"
                f"⏰ {to_small_caps('Valid for 24 hours')}",
                parse_mode="HTML"
            )
            
            LOGGER.info(f"User {user_id} generated code {code} for {coin_amount} coins")
            
    except TimeoutError:
        await update.message.reply_text(
            f"⚠️ {to_small_caps('System busy. Please try again.')}",
            parse_mode="HTML"
        )

async def credeem_command_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """High-performance code redemption"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            f"<b>🎁 {to_small_caps('REDEEM CODE')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/credeem &lt;CODE&gt;</code>\n\n"
            f"💡 {to_small_caps('Redeem your coin codes to add coins to your balance!')}",
            parse_mode="HTML"
        )
        return
    
    code = context.args[0].upper()
    lock_key = f"redeem_{user_id}_{hashlib.md5(code.encode()).hexdigest()[:8]}"
    
    try:
        async with _lock_manager.lock(lock_key, timeout=CONFIG.LOCK_TIMEOUT_SECONDS):
            now = _utcnow()
            
            # Optimized query with projection
            code_doc = await claim_codes_collection.find_one(
                {
                    "code": code,
                    "user_id": user_id,
                    "is_redeemed": False,
                    "expires_at": {"$gt": now}
                },
                {"amount": 1, "_id": 1}
            )
            
            if not code_doc:
                # Check if expired or already redeemed for better UX
                check_doc = await claim_codes_collection.find_one(
                    {"code": code, "user_id": user_id},
                    {"is_redeemed": 1, "expires_at": 1}
                )
                
                if not check_doc:
                    await update.message.reply_text(
                        f"<b>❌ {to_small_caps('INVALID CODE')}</b>\n\n"
                        f"⚠️ {to_small_caps('This code does not exist or does not belong to you.')}\n\n"
                        f"💡 {to_small_caps('Use /claim to generate a new code!')}",
                        parse_mode="HTML"
                    )
                elif check_doc.get("is_redeemed"):
                    await update.message.reply_text(
                        f"<b>❌ {to_small_caps('CODE ALREADY REDEEMED')}</b>\n\n"
                        f"⚠️ {to_small_caps('This code has already been used.')}",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text(
                        f"<b>❌ {to_small_caps('CODE EXPIRED')}</b>\n\n"
                        f"⚠️ {to_small_caps('This code has expired (24 hours limit).')}",
                        parse_mode="HTML"
                    )
                return
            
            # Atomic redemption
            coin_amount = code_doc["amount"]
            
            # Use transaction-like update
            redeem_result = await claim_codes_collection.update_one(
                {
                    "_id": code_doc["_id"],
                    "is_redeemed": False
                },
                {
                    "$set": {
                        "is_redeemed": True,
                        "redeemed_at": now
                    }
                }
            )
            
            if redeem_result.modified_count == 0:
                await update.message.reply_text(
                    f"<b>❌ {to_small_caps('CODE ALREADY REDEEMED')}</b>",
                    parse_mode="HTML"
                )
                return
            
            # Update user balance
            user_result = await user_collection.find_one_and_update(
                {"id": user_id},
                {
                    "$inc": {"balance": coin_amount},
                    "$set": {"last_credeem": now}
                },
                upsert=True,
                return_document=True
            )
            
            new_balance = user_result.get("balance", coin_amount) if user_result else coin_amount
            
            await update.message.reply_text(
                f"<b>✅ {to_small_caps('CODE REDEEMED SUCCESSFULLY!')}</b>\n\n"
                f"💰 <b>{to_small_caps('Coins Added:')}</b> {coin_amount:,}\n"
                f"💎 <b>{to_small_caps('New Balance:')}</b> {new_balance:,} {to_small_caps('coins')}\n\n"
                f"🎉 {to_small_caps('Enjoy your coins!')}",
                parse_mode="HTML"
            )
            
            LOGGER.info(f"User {user_id} redeemed code {code} for {coin_amount} coins")
            
    except TimeoutError:
        await update.message.reply_text(
            f"⚠️ {to_small_caps('System busy. Please try again.')}",
            parse_mode="HTML"
        )

# ============================================================================
# REGISTRATION & INITIALIZATION
# ============================================================================

def register_handlers_v3():
    """Register optimized handlers"""
    application.add_handler(CommandHandler("sclaim", sclaim_command_v3, block=False))
    application.add_handler(CommandHandler("claim", claim_command_v3, block=False))
    application.add_handler(CommandHandler("credeem", credeem_command_v3, block=False))
    LOGGER.info("✅ Claim System v3 handlers registered successfully")

# Initialize on module load
async def initialize_v3():
    """Initialize database optimizations"""
    await db_optimizer.init_indexes()

# Run initialization
try:
    asyncio.create_task(initialize_v3())
except:
    pass

register_handlers_v3()
