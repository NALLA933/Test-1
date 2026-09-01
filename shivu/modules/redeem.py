import secrets
import string
import time
from typing import Any
from html import escape

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from shivu import application, user_collection, collection, db, LOGGER
from shivu.character_ids import (
    character_id_query,
    character_matches_id,
    normalize_character_document,
    normalize_character_id,
)
from shivu.security import is_owner_or_sudo

redeem_codes_collection = db.redeem_codes

from shivu.utils import to_small_caps, get_rarity_display

_redeem_rate_limiter = {}
_RATE_LIMIT_WINDOW = 2
_RATE_LIMIT_MAX = 3

def generate_unique_code(length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    alphabet = alphabet.replace('0', '').replace('o', '').replace('i', '').replace('l', '').replace('1', '')
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    code = f"sanpai-{random_part}"
    return code

def _check_rate_limit(user_id: int) -> bool:
    now = time.time()
    if user_id in _redeem_rate_limiter:
        timestamps = [t for t in _redeem_rate_limiter[user_id] if now - t < _RATE_LIMIT_WINDOW]
        _redeem_rate_limiter[user_id] = timestamps
        if len(timestamps) >= _RATE_LIMIT_MAX:
            return False
        _redeem_rate_limiter[user_id].append(now)
    else:
        _redeem_rate_limiter[user_id] = [now]
    return True

async def _ensure_indexes():
    try:
        await redeem_codes_collection.create_index("code", unique=True)
    except Exception as e:
        LOGGER.error(f"Index creation failed: {e}")

async def create_coin_code(amount: int, max_uses: int, created_by: int) -> str | None:
    if redeem_codes_collection is None:
        LOGGER.error("Redeem codes collection not initialized")
        return None

    try:
        await _ensure_indexes()
        
        for attempt in range(10):
            code = generate_unique_code()
            document = {
                "code": code,
                "type": "coin",
                "amount": int(amount),
                "max_uses": int(max_uses),
                "used_by": [],
                "is_active": True,
                "created_by": int(created_by)
            }
            
            try:
                await redeem_codes_collection.insert_one(document)
                LOGGER.info(f"Created coin code: {code} for {amount} coins, max uses: {max_uses}")
                return code
            except Exception as insert_err:
                if "duplicate key" in str(insert_err).lower() or "E11000" in str(insert_err):
                    continue
                raise
        
        LOGGER.error("Failed to generate unique code after 10 attempts")
        return None
    except Exception as e:
        LOGGER.error(f"Failed to create coin code: {e}")
        return None

async def create_character_code(character_id: int, max_uses: int, created_by: int) -> str | None:
    if redeem_codes_collection is None:
        LOGGER.error("Redeem codes collection not initialized")
        return None

    try:
        character = await collection.find_one(character_id_query(character_id))

        if not character:
            LOGGER.warning(f"Character ID {character_id} not found in anime_characters_lol collection")
            return None

        await _ensure_indexes()
        
        for attempt in range(10):
            code = generate_unique_code()
            document = {
                "code": code,
                "type": "character",
                "character_id": int(character_id),
                "max_uses": int(max_uses),
                "used_by": [],
                "is_active": True,
                "created_by": int(created_by)
            }
            
            try:
                await redeem_codes_collection.insert_one(document)
                LOGGER.info(f"Created character code: {code} for character {character_id}, max uses: {max_uses}")
                return code
            except Exception as insert_err:
                if "duplicate key" in str(insert_err).lower() or "E11000" in str(insert_err):
                    continue
                raise
        
        LOGGER.error("Failed to generate unique code after 10 attempts")
        return None
    except Exception as e:
        LOGGER.error(f"Failed to create character code: {e}")
        return None

async def redeem_code(code: str, user_id: int) -> dict[str, Any]:
    if redeem_codes_collection is None:
        return {"success": False, "message": "❌ System error: database not available"}

    if not _check_rate_limit(user_id):
        return {
            "success": False,
            "message": "⚠️ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ꜰᴇᴡ sᴇᴄᴏɴᴅs ʙᴇꜰᴏʀᴇ ʀᴇᴅᴇᴇᴍɪɴɢ ᴀɢᴀɪɴ.",
            "show_alert": True
        }

    try:
        code_normalized = code.lower()
        
        update_result = await redeem_codes_collection.find_one_and_update(
            {
                "code": code_normalized,
                "is_active": True,
                "used_by": {"$ne": user_id},
                "$expr": {"$lt": [{"$size": "$used_by"}, "$max_uses"]}
            },
            {
                "$push": {"used_by": user_id}
            },
            return_document=True
        )

        if not update_result:
            code_doc = await redeem_codes_collection.find_one({"code": code_normalized})
            
            if not code_doc:
                return {
                    "success": False, 
                    "message": "⚠️ ɪɴᴠᴀʟɪᴅ ᴄᴏᴅᴇ\nᴛʜɪs ᴄᴏᴅᴇ ᴅᴏᴇs ɴᴏᴛ ᴇxɪsᴛ.",
                    "show_alert": True
                }
            
            if not code_doc.get("is_active", False):
                return {
                    "success": False,
                    "message": "❌ ᴛʜɪs ᴄᴏᴅᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ʙᴇᴇɴ ʀᴇᴅᴇᴇᴍᴇᴅ.",
                    "show_alert": True
                }
            
            if user_id in code_doc.get("used_by", []):
                return {
                    "success": False,
                    "message": "⚠️ ʏᴏᴜ ʜᴀᴠᴇ ᴀʟʀᴇᴀᴅʏ ʀᴇᴅᴇᴇᴍᴇᴅ ᴛʜɪs ᴄᴏᴅᴇ.",
                    "show_alert": True
                }
            
            if len(code_doc.get("used_by", [])) >= code_doc.get("max_uses", 1):
                await redeem_codes_collection.update_one(
                    {"code": code_normalized},
                    {"$set": {"is_active": False}}
                )
                return {
                    "success": False,
                    "message": "❌ ᴛʜɪs ᴄᴏᴅᴇ ʜᴀs ᴀʟʀᴇᴀᴅʏ ʙᴇᴇɴ ʀᴇᴅᴇᴇᴍᴇᴅ.",
                    "show_alert": True
                }
            
            return {
                "success": False,
                "message": "❌ ʀᴇᴅᴇᴍᴘᴛɪᴏɴ ꜰᴀɪʟᴇᴅ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.",
                "show_alert": True
            }

        code_type = update_result.get("type")

        if code_type == "coin":
            amount = update_result.get("amount", 0)
            
            try:
                coin_update = await user_collection.update_one(
                    {"id": user_id},
                    {"$inc": {"balance": amount}},
                    upsert=True
                )
                
                if coin_update.modified_count == 0 and coin_update.upserted_id is None and coin_update.matched_count == 0:
                    raise Exception("Failed to update user balance")
                
                if len(update_result.get("used_by", [])) >= update_result.get("max_uses", 1):
                    await redeem_codes_collection.update_one(
                        {"code": code_normalized},
                        {"$set": {"is_active": False}}
                    )
                
                message = (
                    f"<b>✅ {to_small_caps('CODE REDEEMED SUCCESSFULLY!')}</b>\n\n"
                    f"💰 {to_small_caps('Coins Received:')} <b>{amount:,}</b>\n"
                    f"🎉 {to_small_caps('Your new balance has been updated!')}"
                )
                
                return {
                    "success": True,
                    "message": message,
                    "data": {
                        "type": "coin",
                        "amount": amount
                    }
                }
            
            except Exception as reward_error:
                LOGGER.error(f"Reward distribution failed for user {user_id}, code {code_normalized}: {reward_error}")
                
                await redeem_codes_collection.update_one(
                    {"code": code_normalized},
                    {"$pull": {"used_by": user_id}}
                )
                
                return {
                    "success": False,
                    "message": "❌ sʏsᴛᴇᴍ ᴇʀʀᴏʀ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
                    "show_alert": True
                }

        elif code_type == "character":
            character_id = normalize_character_id(update_result.get("character_id"))
            
            try:
                character = await collection.find_one(character_id_query(character_id))
                
                if not character:
                    await redeem_codes_collection.update_one(
                        {"code": code_normalized},
                        {"$pull": {"used_by": user_id}}
                    )
                    
                    return {
                        "success": False,
                        "message": "❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ.",
                        "show_alert": True
                    }
                
                character_name = character.get("name", "Unknown")
                anime_name = character.get("anime", "Unknown")
                rarity = character.get("rarity", 1)
                img_url = character.get("img_url", "")
                
                rarity_display = get_rarity_display(rarity)
                character_data = normalize_character_document({
                    "id": character.get("id", character_id),
                    "name": character_name,
                    "anime": anime_name,
                    "rarity": rarity,
                    "img_url": img_url
                })

                user_doc = await user_collection.find_one({"id": user_id}, {"characters": 1})
                existing_chars = user_doc.get("characters", []) if user_doc else []
                if any(character_matches_id(existing_char, character_id) for existing_char in existing_chars):
                    await redeem_codes_collection.update_one(
                        {"code": code_normalized},
                        {"$pull": {"used_by": user_id}}
                    )
                    return {
                        "success": False,
                        "message": "⚠️ You already own this character.",
                        "show_alert": True
                    }
                
                char_update = await user_collection.update_one(
                    {"id": user_id},
                    {
                        "$push": {"characters": character_data},
                        "$setOnInsert": {"id": user_id}
                    },
                    upsert=True
                )
                
                if char_update.modified_count == 0 and char_update.upserted_id is None:
                    user_doc = await user_collection.find_one({"id": user_id})
                    
                    if user_doc:
                        existing_chars = user_doc.get("characters", [])
                        has_char = any(character_matches_id(c, character_id) for c in existing_chars)
                        
                        if not has_char:
                            raise Exception("Failed to add character to user collection")
                
                if len(update_result.get("used_by", [])) >= update_result.get("max_uses", 1):
                    await redeem_codes_collection.update_one(
                        {"code": code_normalized},
                        {"$set": {"is_active": False}}
                    )
                
                message = (
                    f"<b>✅ {to_small_caps('CHARACTER CODE REDEEMED!')}</b>\n\n"
                    f"🎴 <b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
                    f"📺 <b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
                    f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}\n\n"
                    f"🎉 {to_small_caps('Added to your collection!')}"
                )
                
                return {
                    "success": True,
                    "message": message,
                    "img_url": img_url,
                    "data": {
                        "type": "character",
                        "character_id": character_id,
                        "character_name": character_name
                    }
                }
            
            except Exception as reward_error:
                LOGGER.error(f"Character reward failed for user {user_id}, code {code_normalized}: {reward_error}")
                
                await redeem_codes_collection.update_one(
                    {"code": code_normalized},
                    {"$pull": {"used_by": user_id}}
                )
                
                return {
                    "success": False,
                    "message": "❌ sʏsᴛᴇᴍ ᴇʀʀᴏʀ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
                    "show_alert": True
                }

        else:
            await redeem_codes_collection.update_one(
                {"code": code_normalized},
                {"$pull": {"used_by": user_id}}
            )
            
            return {
                "success": False,
                "message": "❌ ᴜɴᴋɴᴏᴡɴ ᴄᴏᴅᴇ ᴛʏᴘᴇ.",
                "show_alert": True
            }

    except Exception as e:
        LOGGER.error(f"Failed to redeem code {code} for user {user_id}: {e}")
        return {
            "success": False,
            "message": "❌ sʏsᴛᴇᴍ ᴇʀʀᴏʀ. ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.",
            "show_alert": True
        }

async def gen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_owner_or_sudo(user_id):
        await update.message.reply_text("❌ " + to_small_caps("You are not authorized to use this command."))
        return

    if len(context.args) < 2:
        usage_msg = (
            f"<b>💰 {to_small_caps('COIN CODE GENERATOR')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/gen &lt;amount&gt; &lt;max_users&gt;</code>"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return

    try:
        amount = int(context.args[0])
        max_uses = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            f"❌ {to_small_caps('Invalid arguments. Amount and max users must be positive integers.')}"
        )
        return

    if amount <= 0:
        await update.message.reply_text("❌ " + to_small_caps("Amount must be greater than 0."))
        return

    if max_uses <= 0:
        await update.message.reply_text("❌ " + to_small_caps("Max users must be greater than 0."))
        return

    code = await create_coin_code(amount, max_uses, user_id)

    if code:
        response = (
            f"<b>✅ {to_small_caps('COIN CODE GENERATED')}</b>\n\n"
            f"🎟️ <b>{to_small_caps('Code:')}</b> <code>{code}</code>\n"
            f"💎 <b>{to_small_caps('Type:')}</b> {to_small_caps('Coins')}\n"
            f"💰 <b>{to_small_caps('Amount:')}</b> {amount:,} {to_small_caps('coins')}\n"
            f"👥 <b>{to_small_caps('Max Uses:')}</b> {max_uses}"
        )
        await update.message.reply_text(response, parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"❌ {to_small_caps('Failed to generate code. Please try again.')}"
        )

async def sgen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_owner_or_sudo(user_id):
        await update.message.reply_text("❌ " + to_small_caps("You are not authorized to use this command."))
        return

    if len(context.args) < 2:
        usage_msg = (
            f"<b>🎴 {to_small_caps('CHARACTER CODE GENERATOR')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/sgen &lt;character_id&gt; &lt;max_users&gt;</code>"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return

    try:
        character_id = int(context.args[0])
        max_uses = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            f"❌ {to_small_caps('Invalid arguments. Character ID and max users must be positive integers.')}"
        )
        return

    if character_id <= 0:
        await update.message.reply_text("❌ " + to_small_caps("Character ID must be greater than 0."))
        return

    if max_uses <= 0:
        await update.message.reply_text("❌ " + to_small_caps("Max users must be greater than 0."))
        return

    character = await collection.find_one(character_id_query(character_id))

    if not character:
        error_msg = (
            f"❌ {to_small_caps('Character Not Found')}\n\n"
            f"🔍 {to_small_caps(f'The character with ID {character_id} does not exist in the database.')}\n"
            f"💡 {to_small_caps('Please verify the character ID and try again.')}"
        )
        await update.message.reply_text(error_msg, parse_mode="HTML")
        return

    code = await create_character_code(character_id, max_uses, user_id)

    if code:
        character_name = character.get("name", "Unknown")
        anime_name = character.get("anime", "Unknown")
        rarity = character.get("rarity", 1)
        rarity_display = get_rarity_display(rarity)

        response = (
            f"<b>✅ {to_small_caps('CHARACTER CODE GENERATED')}</b>\n\n"
            f"🎟️ <b>{to_small_caps('Code:')}</b> <code>{code}</code>\n"
            f"🎴 <b>{to_small_caps('Type:')}</b> {to_small_caps('Character')}\n"
            f"👤 <b>{to_small_caps('Character:')}</b> {escape(character_name)}\n"
            f"📺 <b>{to_small_caps('Anime:')}</b> {escape(anime_name)}\n"
            f"🆔 <b>{to_small_caps('ID:')}</b> {character_id}\n"
            f"⭐ <b>{to_small_caps('Rarity:')}</b> {rarity_display}\n"
            f"👥 <b>{to_small_caps('Max Uses:')}</b> {max_uses}"
        )
        await update.message.reply_text(response, parse_mode="HTML")

        LOGGER.info(f"Generated character code {code} for ID {character_id} ({character_name}) by user {user_id}")
    else:
        await update.message.reply_text(
            f"❌ {to_small_caps('Failed to generate code. Please try again.')}"
        )

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if len(context.args) < 1:
        usage_msg = (
            f"<b>🎁 {to_small_caps('REDEEM CODE')}</b>\n\n"
            f"📝 {to_small_caps('Usage:')} <code>/redeem &lt;CODE&gt;</code>\n\n"
            f"💡 {to_small_caps('Redeem codes can give you coins or characters!')}"
        )
        await update.message.reply_text(usage_msg, parse_mode="HTML")
        return

    code = context.args[0].lower()

    result = await redeem_code(code, user_id)

    if result["success"]:
        if result.get("img_url"):
            try:
                await update.message.reply_photo(
                    photo=result["img_url"],
                    caption=result["message"],
                    parse_mode="HTML"
                )
            except Exception as e:
                LOGGER.error(f"Failed to send image: {e}")
                await update.message.reply_text(result["message"], parse_mode="HTML")
        else:
            await update.message.reply_text(result["message"], parse_mode="HTML")
    else:
        await update.message.reply_text(result["message"], parse_mode="HTML")

def register_handlers():
    application.add_handler(CommandHandler("gen", gen_command, block=False))
    application.add_handler(CommandHandler("sgen", sgen_command, block=False))
    application.add_handler(CommandHandler("redeem", redeem_command, block=False))
    LOGGER.info("Redeem system handlers registered successfully")

register_handlers()
