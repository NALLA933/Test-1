import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

RARITY_MAP = {
    1: (1, "⚪ ᴄᴏᴍᴍᴏɴ"),
    2: (2, "🔵 ʀᴀʀᴇ"),
    3: (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ"),
    4: (4, "💮 ꜱᴘᴇᴄɪᴀʟ"),
    5: (5, "👹 ᴀɴᴄɪᴇɴᴛ"),
    6: (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ"),
    7: (7, "🔮 ᴇᴘɪᴄ"),
    8: (8, "🪐 ᴄᴏꜱᴍɪᴄ"),
    9: (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ"),
    10: (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ"),
    11: (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ"),
    12: (12, "🌸 ꜱᴘʀɪɴɢ"),
    13: (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ"),
    14: (14, "🍭 ᴋᴀᴡᴀɪɪ"),
    15: (15, "🧬 ʜʏʙʀɪᴅ"),
}

WRONG_FORMAT_TEXT = """❌ Wrong format!

Usage: /upload <img_url> <character-name> <anime-name> <rarity>

Example: /upload https://example.com/image.jpg muzan-kibutsuji demon-slayer 3

Rarity Numbers:
1-⚪ ᴄᴏᴍᴍᴏɴ | 2-🔵 ʀᴀʀᴇ | 3-🟡 ʟᴇɢᴇɴᴅᴀʀʏ | 4-💮 ꜱᴘᴇᴄɪᴀʟ
5-👹 ᴀɴᴄɪᴇɴᴛ | 6-🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ | 7-🔮 ᴇᴘɪᴄ | 8-🪐 ᴄᴏꜱᴍɪᴄ
9-⚰️ ɴɪɢʜᴛᴍᴀʀᴇ | 10-🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ | 11-💝 ᴠᴀʟᴇɴᴛɪɴᴇ | 12-🌸 ꜱᴘʀɪɴɢ
13-🏖️ ᴛʀᴏᴘɪᴄᴀʟ | 14-🍭 ᴋᴀᴡᴀɪɪ | 15-🧬 ʜʏʙʀɪᴅ"""

MAX_FILE_SIZE = 10 * 1024 * 1024

async def get_next_sequence_number(sequence_name):
    sequence_collection = db.sequences
    sequence_document = await sequence_collection.find_one_and_update(
        {'_id': sequence_name}, 
        {'$inc': {'sequence_value': 1}}, 
        return_document=ReturnDocument.AFTER
    )
    if not sequence_document:
        await sequence_collection.insert_one({'_id': sequence_name, 'sequence_value': 0})
        return 0
    return sequence_document['sequence_value']

async def validate_url(url):
    if not url.startswith(('http://', 'https://')):
        return False, "Invalid URL format"
    
    if 'telegra.ph' in url.lower() or 't.me/' in url.lower():
        return False, "Telegraph and Telegram file IDs are not supported"
    
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as response:
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > MAX_FILE_SIZE:
                        return False, f"Image size ({int(content_length) / (1024*1024):.2f} MB) exceeds 10 MB limit"
                    return True, None
            except:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=25), allow_redirects=True) as response:
                        if response.status == 200:
                            content = await response.read()
                            if len(content) > MAX_FILE_SIZE:
                                return False, f"Image size ({len(content) / (1024*1024):.2f} MB) exceeds 10 MB limit"
                            return True, None
                except:
                    pass
        return True, None
    except Exception:
        return True, None

async def upload(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        args = context.args
        if len(args) != 4:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        img_url = args[0]
        character_name = args[1].replace('-', ' ').title()
        anime = args[2].replace('-', ' ').title()

        try:
            rarity_num = int(args[3])
            if rarity_num not in RARITY_MAP:
                await update.message.reply_text(f'Invalid rarity. Use numbers 1-15.\n\n{WRONG_FORMAT_TEXT}')
                return
            rarity_value, rarity_name = RARITY_MAP[rarity_num]
        except ValueError:
            await update.message.reply_text('Rarity must be a number!')
            return

        existing = await collection.find_one({'img_url': img_url})
        if existing:
            await update.message.reply_text(f'❌ This image URL already exists in database!\nCharacter: {existing["name"]}\nID: {existing["id"]}')
            return

        is_valid, error_msg = await validate_url(img_url)
        if not is_valid:
            await update.message.reply_text(f'❌ URL Validation Failed: {error_msg}')
            return

        id = str(await get_next_sequence_number('character_id')).zfill(2)

        try:
            rarity_emoji = rarity_name.split()[0]
            rarity_text = ' '.join(rarity_name.split()[1:])
            
            caption = (
                f"{id}: {character_name}\n"
                f"{anime}\n"
                f"{rarity_emoji}𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n\n"
                f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={update.effective_user.id}'>{update.effective_user.first_name}</a>"
            )
            
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=caption,
                parse_mode='HTML'
            )
            
            character = {
                'img_url': img_url,
                'name': character_name,
                'anime': anime,
                'rarity': rarity_name,
                'id': id,
                'message_id': message.message_id
            }
            
            await collection.insert_one(character)
            await update.message.reply_text('✅ CHARACTER ADDED SUCCESSFULLY!')
            
        except Exception as e:
            await update.message.reply_text(f'❌ Failed to upload to channel: {str(e)}')

    except Exception as e:
        await update.message.reply_text(f'❌ Upload failed: {str(e)}\n\nReport to: {SUPPORT_CHAT}')

async def delete(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('❌ Incorrect format!\n\nUsage: /delete <ID>')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if character:
            try:
                await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
                await update.message.reply_text('✅ Character deleted successfully!')
            except:
                await update.message.reply_text('✅ Deleted from database, but failed to delete from channel')
        else:
            await update.message.reply_text('❌ Character not found!')
            
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')

async def update_character(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in Config.SUDO_USERS and user_id != str(Config.OWNER_ID):
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('❌ Incorrect format!\n\nUsage: /update <id> <field> <new_value>\nFields: img_url, name, anime, rarity')
            return

        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text('❌ Character not found!')
            return

        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'❌ Invalid field! Use: {", ".join(valid_fields)}')
            return

        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()
        elif args[1] == 'rarity':
            try:
                rarity_num = int(args[2])
                if rarity_num not in RARITY_MAP:
                    await update.message.reply_text(f'❌ Invalid rarity! Use 1-15')
                    return
                _, new_value = RARITY_MAP[rarity_num]
            except ValueError:
                await update.message.reply_text('❌ Rarity must be a number!')
                return
        else:
            new_value = args[2]

        if args[1] == 'img_url':
            is_valid, error_msg = await validate_url(new_value)
            if not is_valid:
                await update.message.reply_text(f'❌ URL Validation Failed: {error_msg}')
                return
            
            existing = await collection.find_one({'img_url': new_value, 'id': {'$ne': args[0]}})
            if existing:
                await update.message.reply_text(f'❌ This URL already exists!\nCharacter: {existing["name"]}\nID: {existing["id"]}')
                return

        await collection.find_one_and_update({'id': args[0]}, {'$set': {args[1]: new_value}})
        
        updated_char = await collection.find_one({'id': args[0]})
        
        rarity_emoji = updated_char['rarity'].split()[0]
        rarity_text = ' '.join(updated_char['rarity'].split()[1:])
        
        caption = (
            f"{updated_char['id']}: {updated_char['name']}\n"
            f"{updated_char['anime']}\n"
            f"{rarity_emoji}𝙍𝘼𝙍𝙄𝙏𝙔: {rarity_text}\n\n"
            f"𝑴𝒂𝒅𝒆 𝑩𝒚 ➥ <a href='tg://user?id={update.effective_user.id}'>{update.effective_user.first_name}</a>"
        )

        if args[1] == 'img_url':
            try:
                message = await context.bot.edit_message_media(
                    chat_id=CHARA_CHANNEL_ID,
                    message_id=character['message_id'],
                    media={'type': 'photo', 'media': new_value, 'caption': caption, 'parse_mode': 'HTML'}
                )
                await update.message.reply_text('✅ Image URL updated successfully!')
            except:
                await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
                message = await context.bot.send_photo(
                    chat_id=CHARA_CHANNEL_ID,
                    photo=new_value,
                    caption=caption,
                    parse_mode='HTML'
                )
                await collection.find_one_and_update({'id': args[0]}, {'$set': {'message_id': message.message_id}})
                await update.message.reply_text('✅ Updated with new message!')
        else:
            await context.bot.edit_message_caption(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id'],
                caption=caption,
                parse_mode='HTML'
            )
            await update.message.reply_text('✅ Updated successfully!')

    except Exception as e:
        await update.message.reply_text(f'❌ Update failed: {str(e)}')

UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)
DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)
UPDATE_HANDLER = CommandHandler('update', update_character, block=False)
application.add_handler(UPDATE_HANDLER)
