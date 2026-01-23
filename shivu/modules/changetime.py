import importlib
import time
import random
import re
import asyncio
from html import escape
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    filters,
    JobQueue
)
from pymongo import MongoClient, errors
from pymongo.collection import Collection
from bson import ObjectId


class Rarity(Enum):
    """Character rarity levels with spawn weights"""
    COMMON = "Common"
    RARE = "Rare"
    SUPER_RARE = "Super Rare"
    ULTRA_RARE = "Ultra Rare"
    LEGENDARY = "Legendary"
    CELESTIAL = "Celestial"

    @classmethod
    def get_spawn_weight(cls, rarity: str) -> int:
        """Get spawn probability weight for each rarity"""
        weights = {
            cls.COMMON.value: 40,
            cls.RARE.value: 25,
            cls.SUPER_RARE.value: 15,
            cls.ULTRA_RARE.value: 10,
            cls.LEGENDARY.value: 7,
            cls.CELESTIAL.value: 3
        }
        return weights.get(rarity, 40)


class GameManager:
    """Main game manager class handling all bot functionality"""
    
    def __init__(self, token: str, mongo_uri: str, db_name: str):
        """Initialize the game manager with database and state"""
        self.token = token
        self.db_name = db_name
        
        # MongoDB setup
        self.client = MongoClient(mongo_uri)
        self.db = self.client[self.db_name]
        
        # Collections
        self.characters: Collection = self.db.characters
        self.users: Collection = self.db.users
        self.groups: Collection = self.db.groups
        self.user_balance: Collection = self.db.user_balance
        self.group_stats: Collection = self.db.group_stats
        self.user_group_stats: Collection = self.db.user_group_stats
        self.pity_counters: Collection = self.db.pity_counters
        
        # In-memory state (minimal)
        self.active_spawns: Dict[int, Dict] = {}  # chat_id -> character_data
        self.first_correct_guesses: Dict[int, int] = {}  # chat_id -> user_id
        self.message_cooldowns: Dict[Tuple[int, int], float] = {}  # (chat_id, user_id) -> timestamp
        self.chat_locks: Dict[int, asyncio.Lock] = {}
        
        # Constants
        self.SUPPORT_CHAT = "@your_support_chat"
        self.UPDATE_CHAT = "@your_update_chat"
        self.LOGGER = None  # Set your logger
        
        # Spawn configuration
        self.MIN_MESSAGES = 80
        self.MAX_MESSAGES = 150
        self.COIN_REWARD_RANGE = (1, 5)
        self.COIN_COOLDOWN = 30  # seconds
        self.PITY_THRESHOLD = 50
        
        # Initialize application
        self.application = ApplicationBuilder().token(self.token).build()
        
    def setup_indexes(self) -> None:
        """Setup database indexes with proper error handling"""
        indexes = [
            # Characters collection
            (self.characters, [("id", 1)], {"unique": True}),
            (self.characters, [("rarity", 1)]),
            (self.characters, [("name", "text"), ("anime", "text")]),
            
            # Users collection
            (self.users, [("id", 1)], {"unique": True}),
            (self.users, [("characters.id", 1)]),
            
            # User balance
            (self.user_balance, [("user_id", 1), ("chat_id", 1)], {"unique": True}),
            
            # Groups
            (self.groups, [("group_id", 1)], {"unique": True}),
            
            # Group stats
            (self.group_stats, [("group_id", 1)], {"unique": True}),
            
            # Pity counters
            (self.pity_counters, [("group_id", 1)], {"unique": True}),
            
            # User group stats
            (self.user_group_stats, [("user_id", 1), ("group_id", 1)], {"unique": True}),
        ]
        
        for collection, keys, kwargs in indexes:
            try:
                collection.create_index(keys, **kwargs)
                self._log(f"Created index on {collection.name}: {keys}")
            except errors.OperationFailure as e:
                if "already exists" not in str(e):
                    self._log(f"Failed to create index on {collection.name}: {e}")
            except errors.CollectionInvalid as e:
                self._log(f"Collection error on {collection.name}: {e}")
    
    @staticmethod
    def to_small_caps(text: str) -> str:
        """Convert text to small caps effect using Unicode"""
        normal = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        small_caps = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
        trans = str.maketrans(normal, small_caps)
        return text.translate(trans)
    
    def _log(self, message: str):
        """Logging utility"""
        if self.LOGGER:
            self.LOGGER.info(message)
        else:
            print(f"[LOG] {message}")
    
    async def spawn_character(self, context: CallbackContext, chat_id: int) -> None:
        """Spawn a character in a chat with pity system"""
        async with self._get_lock(chat_id):
            # Check pity system
            pity_data = await self.pity_counters.find_one({"group_id": chat_id})
            pity_counter = pity_data.get("count", 0) if pity_data else 0
            
            # Get all characters
            all_chars = await self.characters.find({}).to_list(length=None)
            
            # Filter by sent characters in this chat
            sent_chars = self._get_sent_characters(chat_id)
            available_chars = [c for c in all_chars if c['id'] not in sent_chars]
            
            if not available_chars:
                # Reset sent characters if all have been shown
                self._reset_sent_characters(chat_id)
                available_chars = all_chars
            
            # Force high rarity if pity threshold reached
            if pity_counter >= self.PITY_THRESHOLD:
                high_rarity = [Rarity.LEGENDARY.value, Rarity.CELESTIAL.value]
                forced_chars = [c for c in available_chars if c['rarity'] in high_rarity]
                if forced_chars:
                    character = random.choice(forced_chars)
                    await self.pity_counters.update_one(
                        {"group_id": chat_id},
                        {"$set": {"count": 0}},
                        upsert=True
                    )
                    self._log(f"Pity system activated in chat {chat_id}: {character['rarity']}")
                else:
                    character = random.choice(available_chars)
            else:
                # Weighted random selection based on rarity
                weighted_chars = []
                for char in available_chars:
                    weight = Rarity.get_spawn_weight(char['rarity'])
                    weighted_chars.extend([char] * weight)
                
                character = random.choice(weighted_chars)
                
                # Update pity counter
                if character['rarity'] == Rarity.COMMON.value:
                    new_count = pity_counter + 1
                    await self.pity_counters.update_one(
                        {"group_id": chat_id},
                        {"$set": {"count": new_count}},
                        upsert=True
                    )
                else:
                    await self.pity_counters.update_one(
                        {"group_id": chat_id},
                        {"$set": {"count": 0}},
                        upsert=True
                    )
            
            # Store active spawn
            self.active_spawns[chat_id] = character
            self.first_correct_guesses.pop(chat_id, None)
            
            # Add to sent characters
            self._add_sent_character(chat_id, character['id'])
            
            # Update group stats
            await self.group_stats.update_one(
                {"group_id": chat_id},
                {
                    "$inc": {"spawn_count": 1},
                    "$set": {
                        "group_name": context.bot.get_chat(chat_id).title,
                        "last_spawn": datetime.now()
                    }
                },
                upsert=True
            )
            
            # Format message with enhanced UI
            caption = self._format_spawn_message(character)
            
            # Send character image
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=character['img_url'],
                caption=caption,
                parse_mode='HTML'
            )
    
    def _format_spawn_message(self, character: Dict) -> str:
        """Format spawn message with HTML styling"""
        rarity_colors = {
            Rarity.COMMON.value: "#808080",
            Rarity.RARE.value: "#1E90FF",
            Rarity.SUPER_RARE.value: "#9B30FF",
            Rarity.ULTRA_RARE.value: "#FF4500",
            Rarity.LEGENDARY.value: "#FFD700",
            Rarity.CELESTIAL.value: "#00FFFF"
        }
        
        color = rarity_colors.get(character['rarity'], "#FFFFFF")
        
        return (
            f"✨ <b>A New Character Has Appeared!</b> ✨\n\n"
            f"<b>Name:</b> <code>{escape(character['name'])}</code>\n"
            f"<b>Anime:</b> <i>{escape(character['anime'])}</i>\n"
            f"<b>Rarity:</b> <span style='color:{color}'><b>{character['rarity']}</b></span>\n\n"
            f"Use <code>/guess Character Name</code> to add to your harem!"
        )
    
    def _format_grab_message(self, user_name: str, character: Dict, is_duplicate: bool = False, 
                           coins_awarded: int = 0) -> str:
        """Format character grab message"""
        if is_duplicate:
            return (
                f"🎯 <b>{escape(user_name)}</b> caught a duplicate!\n\n"
                f"<b>Character:</b> <code>{escape(character['name'])}</code>\n"
                f"<b>Converted to:</b> <b>🪙 {coins_awarded} coins</b>\n\n"
                f"<i>You already have this character in your collection!</i>"
            )
        
        rarity_colors = {
            Rarity.COMMON.value: "#808080",
            Rarity.RARE.value: "#1E90FF",
            Rarity.SUPER_RARE.value: "#9B30FF",
            Rarity.ULTRA_RARE.value: "#FF4500",
            Rarity.LEGENDARY.value: "#FFD700",
            Rarity.CELESTIAL.value: "#00FFFF"
        }
        
        color = rarity_colors.get(character['rarity'], "#FFFFFF")
        
        return (
            f"✅ <b>{escape(user_name)}</b> successfully caught!\n\n"
            f"<b>Name:</b> <code>{escape(character['name'])}</code>\n"
            f"<b>Anime:</b> <i>{escape(character['anime'])}</i>\n"
            f"<b>Rarity:</b> <span style='color:{color}'><b>{character['rarity']}</b></span>\n\n"
            f"Added to your harem! Use /harem to view your collection."
        )
    
    async def handle_message(self, update: Update, context: CallbackContext) -> None:
        """Handle incoming messages and award coins"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Check if it's a private chat (no spawning in PMs)
        if update.effective_chat.type == "private":
            return
        
        # Award coins with cooldown
        await self._award_message_coins(chat_id, user_id)
        
        # Update message count for spawn triggering
        current_count = await self._increment_message_count(chat_id)
        
        # Get spawn threshold for this chat
        threshold = await self._get_spawn_threshold(chat_id)
        
        # Spawn character if threshold reached
        if current_count >= threshold:
            await self.spawn_character(context, chat_id)
            await self._reset_message_count(chat_id)
    
    async def _award_message_coins(self, chat_id: int, user_id: int) -> None:
        """Award random coins for messages with cooldown"""
        cooldown_key = (chat_id, user_id)
        current_time = time.time()
        
        # Check cooldown
        if cooldown_key in self.message_cooldowns:
            if current_time - self.message_cooldowns[cooldown_key] < self.COIN_COOLDOWN:
                return
        
        # Award random coins
        coins = random.randint(*self.COIN_REWARD_RANGE)
        
        await self.user_balance.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$inc": {"balance": coins}},
            upsert=True
        )
        
        # Update cooldown
        self.message_cooldowns[cooldown_key] = current_time
    
    async def _increment_message_count(self, chat_id: int) -> int:
        """Increment and return message count for chat"""
        result = await self.group_stats.update_one(
            {"group_id": chat_id},
            {"$inc": {"message_count": 1}},
            upsert=True
        )
        
        doc = await self.group_stats.find_one({"group_id": chat_id})
        return doc.get("message_count", 0) if doc else 0
    
    async def _reset_message_count(self, chat_id: int) -> None:
        """Reset message count for chat"""
        await self.group_stats.update_one(
            {"group_id": chat_id},
            {"$set": {"message_count": 0}},
            upsert=True
        )
    
    async def _get_spawn_threshold(self, chat_id: int) -> int:
        """Get spawn threshold for chat (customizable per chat)"""
        doc = await self.group_stats.find_one({"group_id": chat_id})
        if doc and "spawn_threshold" in doc:
            return doc["spawn_threshold"]
        
        # Default random threshold
        threshold = random.randint(self.MIN_MESSAGES, self.MAX_MESSAGES)
        await self.group_stats.update_one(
            {"group_id": chat_id},
            {"$set": {"spawn_threshold": threshold}},
            upsert=True
        )
        return threshold
    
    async def handle_guess(self, update: Update, context: CallbackContext) -> None:
        """Handle character guessing"""
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        # Check if there's an active spawn
        if chat_id not in self.active_spawns:
            await update.message.reply_text("❌ No active character to guess!")
            return
        
        # Check if already guessed
        if chat_id in self.first_correct_guesses:
            guesser_id = self.first_correct_guesses[chat_id]
            if guesser_id != user_id:
                await update.message.reply_text("❌ This character was already caught by someone else!")
                return
        
        # Get guess
        guess_text = ' '.join(context.args).lower() if context.args else ''
        
        # Validate guess
        if not guess_text or "()" in guess_text or "&" in guess_text.lower():
            await update.message.reply_text("❌ Invalid guess format!")
            return
        
        # Get current character
        character = self.active_spawns[chat_id]
        character_name = character['name'].lower()
        
        # Simple name matching (can be improved)
        name_parts = character_name.split()
        guess_parts = guess_text.split()
        
        # Check if guess matches (allow partial matches)
        is_correct = False
        if sorted(name_parts) == sorted(guess_parts):
            is_correct = True
        elif any(part in character_name for part in guess_parts if len(part) > 2):
            is_correct = True
        elif any(part in guess_text for part in name_parts if len(part) > 2):
            is_correct = True
        
        if not is_correct:
            await update.message.reply_text("❌ Incorrect guess! Try again.")
            return
        
        # Mark as guessed
        self.first_correct_guesses[chat_id] = user_id
        
        # Check if user already has this character
        user_data = await self.users.find_one({"id": user_id})
        has_character = False
        
        if user_data:
            user_chars = user_data.get("characters", [])
            has_character = any(c['id'] == character['id'] for c in user_chars)
        
        if has_character:
            # Duplicate: Award coins instead
            coin_value = self._calculate_character_value(character['rarity'])
            coins_awarded = int(coin_value * 0.5)  # 50% of value
            
            await self.user_balance.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {"$inc": {"balance": coins_awarded}},
                upsert=True
            )
            
            # Send duplicate message
            message = self._format_grab_message(
                user_name, character, 
                is_duplicate=True, 
                coins_awarded=coins_awarded
            )
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "💰 Check Balance",
                        callback_data=f"balance_{user_id}"
                    )
                ]])
            )
        else:
            # New character: Add to collection
            await self._add_character_to_user(user_id, character, update)
            
            # Update user group stats
            await self.user_group_stats.update_one(
                {"user_id": user_id, "group_id": chat_id},
                {
                    "$inc": {"count": 1},
                    "$set": {
                        "username": update.effective_user.username,
                        "first_name": update.effective_user.first_name,
                        "last_active": datetime.now()
                    }
                },
                upsert=True
            )
            
            # Send success message
            message = self._format_grab_message(user_name, character)
            
            keyboard = [[
                InlineKeyboardButton(
                    "👑 View Harem",
                    switch_inline_query_current_chat=f"collection.{user_id}"
                )
            ]]
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        # Clear active spawn
        self.active_spawns.pop(chat_id, None)
    
    def _calculate_character_value(self, rarity: str) -> int:
        """Calculate coin value based on rarity"""
        values = {
            Rarity.COMMON.value: 50,
            Rarity.RARE.value: 100,
            Rarity.SUPER_RARE.value: 250,
            Rarity.ULTRA_RARE.value: 500,
            Rarity.LEGENDARY.value: 1000,
            Rarity.CELESTIAL.value: 2500
        }
        return values.get(rarity, 50)
    
    async def _add_character_to_user(self, user_id: int, character: Dict, update: Update) -> None:
        """Add character to user's collection"""
        # Update or create user document
        await self.users.update_one(
            {"id": user_id},
            {
                "$push": {"characters": character},
                "$set": {
                    "username": update.effective_user.username,
                    "first_name": update.effective_user.first_name,
                    "last_active": datetime.now()
                }
            },
            upsert=True
        )
    
    async def handle_top(self, update: Update, context: CallbackContext) -> None:
        """Handle /top command for leaderboards"""
        args = context.args or []
        
        if not args or args[0].lower() not in ["users", "groups"]:
            await update.message.reply_text(
                "📊 <b>Leaderboards</b>\n\n"
                "Usage:\n"
                "<code>/top users</code> - Top 10 users by collection\n"
                "<code>/top groups</code> - Top 10 groups by activity",
                parse_mode='HTML'
            )
            return
        
        leaderboard_type = args[0].lower()
        
        if leaderboard_type == "users":
            await self._show_user_leaderboard(update)
        elif leaderboard_type == "groups":
            await self._show_group_leaderboard(update)
    
    async def _show_user_leaderboard(self, update: Update) -> None:
        """Show top 10 users by unique character count"""
        # Using aggregation for efficiency
        pipeline = [
            {"$project": {
                "user_id": "$id",
                "username": 1,
                "first_name": 1,
                "character_count": {"$size": {"$ifNull": ["$characters", []]}}
            }},
            {"$sort": {"character_count": -1}},
            {"$limit": 10}
        ]
        
        top_users = await self.users.aggregate(pipeline).to_list(length=10)
        
        if not top_users:
            await update.message.reply_text("📭 No users found!")
            return
        
        message = "🏆 <b>Top 10 Users</b> 🏆\n\n"
        
        for i, user in enumerate(top_users, 1):
            username = user.get('username', user.get('first_name', 'Anonymous'))
            count = user.get('character_count', 0)
            
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
            
            message += f"{medal} <b>{escape(username)}</b>\n"
            message += f"   📦 Characters: <code>{count}</code>\n\n"
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def _show_group_leaderboard(self, update: Update) -> None:
        """Show top 10 groups by spawn count"""
        top_groups = await self.group_stats.find(
            {},
            {"_id": 0, "group_id": 1, "group_name": 1, "spawn_count": 1}
        ).sort("spawn_count", -1).limit(10).to_list(length=10)
        
        if not top_groups:
            await update.message.reply_text("📭 No groups found!")
            return
        
        message = "🏆 <b>Top 10 Groups</b> 🏆\n\n"
        
        for i, group in enumerate(top_groups, 1):
            group_name = group.get('group_name', f"Group {group.get('group_id')}")
            spawn_count = group.get('spawn_count', 0)
            
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
            
            message += f"{medal} <b>{escape(group_name)}</b>\n"
            message += f"   ✨ Spawns: <code>{spawn_count}</code>\n\n"
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def handle_fav(self, update: Update, context: CallbackContext) -> None:
        """Handle /fav command to favorite a character"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text("❌ Please provide a character ID!")
            return
        
        char_id = context.args[0]
        
        # Check if user has this character
        user_data = await self.users.find_one(
            {"id": user_id, "characters.id": char_id},
            {"characters.$": 1}
        )
        
        if not user_data:
            await update.message.reply_text("❌ You don't have this character!")
            return
        
        # Update favorite
        await self.users.update_one(
            {"id": user_id},
            {"$set": {"favorite_id": char_id}}
        )
        
        char_name = user_data['characters'][0]['name']
        await update.message.reply_text(f"⭐ {char_name} added to favorites!")
    
    async def handle_balance(self, update: Update, context: CallbackContext) -> None:
        """Check user's coin balance"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        balance_data = await self.user_balance.find_one(
            {"user_id": user_id, "chat_id": chat_id}
        )
        
        balance = balance_data.get("balance", 0) if balance_data else 0
        
        await update.message.reply_text(
            f"💰 <b>Your Balance</b>\n\n"
            f"🪙 <b>Coins:</b> <code>{balance}</code>\n\n"
            f"<i>Earn coins by chatting and catching duplicates!</i>",
            parse_mode='HTML'
        )
    
    def _get_lock(self, chat_id: int) -> asyncio.Lock:
        """Get or create a lock for a chat"""
        if chat_id not in self.chat_locks:
            self.chat_locks[chat_id] = asyncio.Lock()
        return self.chat_locks[chat_id]
    
    def _get_sent_characters(self, chat_id: int) -> List[str]:
        """Get sent characters for a chat (simplified - in production, store in DB)"""
        # In production, store this in MongoDB
        return []
    
    def _add_sent_character(self, chat_id: int, char_id: str) -> None:
        """Add character to sent list (simplified)"""
        # In production, store this in MongoDB
        pass
    
    def _reset_sent_characters(self, chat_id: int) -> None:
        """Reset sent characters for a chat (simplified)"""
        # In production, store this in MongoDB
        pass
    
    def setup_handlers(self) -> None:
        """Setup all bot handlers"""
        # Command handlers
        self.application.add_handler(
            CommandHandler(["guess", "protecc", "collect", "grab", "hunt"], self.handle_guess)
        )
        self.application.add_handler(CommandHandler("fav", self.handle_fav))
        self.application.add_handler(CommandHandler("top", self.handle_top))
        self.application.add_handler(CommandHandler(["balance", "coins"], self.handle_balance))
        
        # Message handler for counting and coin rewards
        self.application.add_handler(
            MessageHandler(filters.ALL & ~filters.COMMAND, self.handle_message)
        )
    
    async def run(self) -> None:
        """Run the bot"""
        # Setup database indexes
        self.setup_indexes()
        
        # Setup handlers
        self.setup_handlers()
        
        # Start the bot
        self._log("Starting bot...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        # Keep running
        await asyncio.Event().wait()


# Main execution
async def main():
    # Configuration
    TOKEN = "YOUR_BOT_TOKEN"
    MONGO_URI = "mongodb://localhost:27017/"
    DB_NAME = "waifu_catcher"
    
    # Create and run game manager
    game = GameManager(TOKEN, MONGO_URI, DB_NAME)
    await game.run()


if __name__ == "__main__":
    asyncio.run(main())
