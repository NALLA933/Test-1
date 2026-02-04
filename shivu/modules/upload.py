import io
from enum import Enum
from pymongo import ReturnDocument
import aiohttp

from telegram import Update
from telegram.ext import CommandHandler, CallbackContext

from shivu import application, collection, db, CHARA_CHANNEL_ID, SUPPORT_CHAT
from shivu.config import Config

class RarityLevel(Enum):
    COMMON = (1, "⚪ ᴄᴏᴍᴍᴏɴ")
    RARE = (2, "🔵 ʀᴀʀᴇ")
    LEGENDARY = (3, "🟡 ʟᴇɢᴇɴᴅᴀʀʏ")
    SPECIAL = (4, "💮 ꜱᴘᴇᴄɪᴀʟ")
    ANCIENT = (5, "👹 ᴀɴᴄɪᴇɴᴛ")
    CELESTIAL = (6, "🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ")
    EPIC = (7, "🔮 ᴇᴘɪᴄ")
    COSMIC = (8, "🪐 ᴄᴏꜱᴍɪᴄ")
    NIGHTMARE = (9, "⚰️ ɴɪɢʜᴛᴍᴀʀᴇ")
    FROSTBORN = (10, "🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ")
    VALENTINE = (11, "💝 ᴠᴀʟᴇɴᴛɪɴᴇ")
    SPRING = (12, "🌸 ꜱᴘʀɪɴɢ")
    TROPICAL = (13, "🏖️ ᴛʀᴏᴘɪᴄᴀʟ")
    KAWAII = (14, "🍭 ᴋᴀᴡᴀɪɪ")
    HYBRID = (15, "🧬 ʜʏʙʀɪᴅ")

    @classmethod
    def get_by_number(cls, number):
        for rarity in cls:
            if rarity.value[0] == number:
                return rarity
        return None

WRONG_FORMAT_TEXT = """Wrong ❌️ format...  eg. /upload character-name anime-name rarity-number

Reply to an image with:
/upload character-name anime-name rarity-number

Available rarities:
1 - ⚪ ᴄᴏᴍᴍᴏɴ
2 - 🔵 ʀᴀʀᴇ
3 - 🟡 ʟᴇɢᴇɴᴅᴀʀʏ
4 - 💮 ꜱᴘᴇᴄɪᴀʟ
5 - 👹 ᴀɴᴄɪᴇɴᴛ
6 - 🎐 ᴄᴇʟᴇꜱᴛɪᴀʟ
7 - 🔮 ᴇᴘɪᴄ
8 - 🪐 ᴄᴏꜱᴍɪᴄ
9 - ⚰️ ɴɪɢʜᴛᴍᴀʀᴇ
10 - 🌬️ ꜰʀᴏꜱᴛʙᴏʀɴ
11 - 💝 ᴠᴀʟᴇɴᴛɪɴᴇ
12 - 🌸 ꜱᴘʀɪɴɢ
13 - 🏖️ ᴛʀᴏᴘɪᴄᴀʟ
14 - 🍭 ᴋᴀᴡᴀɪɪ
15 - 🧬 ʜʏʙʀɪᴅ"""

IMGBB_API = "https://api.imgbb.com/1/upload"
IMGBB_API_KEY = "6d52008ec9026912f9f50c8ca96a09c3"
TELEGRAPH_API = "https://telegra.ph/upload"
CATBOX_API = "https://catbox.moe/user/api.php"

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

async def upload_to_imgbb(image_data):
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', image_data)
            data.add_field('key', IMGBB_API_KEY)

            async with session.post(IMGBB_API, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result['data']['url']
    except Exception:
        pass
    return None

async def upload_to_telegraph(image_data):
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('file', image_data, filename='image.jpg')

            async with session.post(TELEGRAPH_API, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return f"https://telegra.ph{result[0]['src']}"
    except Exception:
        pass
    return None

async def upload_to_catbox(image_data):
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('reqtype', 'fileupload')
            data.add_field('fileToUpload', image_data, filename='image.jpg')

            async with session.post(CATBOX_API, data=data) as response:
                if response.status == 200:
                    url = await response.text()
                    return url.strip()
    except Exception:
        pass
    return None

async def upload_image_with_failover(image_data):
    url = await upload_to_imgbb(image_data)
    if url:
        return url

    image_data.seek(0)
    url = await upload_to_telegraph(image_data)
    if url:
        return url

    image_data.seek(0)
    url = await upload_to_catbox(image_data)
    if url:
        return url

    return None

async def upload(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)

    if user_id != str(Config.OWNER_ID) and user_id not in [str(uid) for uid in Config.SUDO_USERS]:
        await update.message.reply_text('Ask My Owner...')
        return

    try:
        if not update.message.reply_to_message or not update.message.reply_to_message.photo:
            await update.message.reply_text('❌ Please reply to an image with the upload command!')
            return

        args = context.args
        if len(args) != 3:
            await update.message.reply_text(WRONG_FORMAT_TEXT)
            return

        character_name = args[0].replace('-', ' ').title()
        anime = args[1].replace('-', ' ').title()

        try:
            rarity_number = int(args[2])
        except ValueError:
            await update.message.reply_text('❌ Rarity must be a number between 1-15.')
            return

        rarity_level = RarityLevel.get_by_number(rarity_number)
        if not rarity_level:
            await update.message.reply_text(f'❌ Invalid rarity number. Please use 1-15.\n\n{WRONG_FORMAT_TEXT}')
            return

        rarity = rarity_level.value[1]

        status_msg = await update.message.reply_text('⏳ Downloading image...')

        photo = update.message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        await status_msg.edit_text('⏳ Uploading image to hosting service...')

        img_url = await upload_image_with_failover(io.BytesIO(image_bytes))

        if not img_url:
            await status_msg.edit_text('❌ Failed to upload image. All hosting services failed. Please try again.')
            return

        await status_msg.edit_text('⏳ Creating character entry...')

        sequence_num = await get_next_sequence_number('character_id')
        id = str(sequence_num).zfill(2) if sequence_num < 100 else str(sequence_num)

        character = {
            'img_url': img_url,
            'name': character_name,
            'anime': anime,
            'rarity': rarity,
            'id': id
        }

        try:
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=img_url,
                caption=f'<b>Character Name:</b> {character_name}\n<b>Anime Name:</b> {anime}\n<b>Rarity:</b> {rarity}\n<b>ID:</b> {id}\nAdded by <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>',
                parse_mode='HTML',
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60,
                pool_timeout=60
            )
            character['message_id'] = message.message_id
            await collection.insert_one(character)
            await status_msg.edit_text('✅ CHARACTER ADDED....')
        except Exception as e:
            await status_msg.edit_text(f'❌ Failed to post to channel. Character not added to database.\nError: {str(e)}')

    except Exception as e:
        await update.message.reply_text(f'Character Upload Unsuccessful. Error: {str(e)}\nIf you think this is a source error, forward to: {SUPPORT_CHAT}')

async def delete(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)

    if user_id != str(Config.OWNER_ID) and user_id not in [str(uid) for uid in Config.SUDO_USERS]:
        await update.message.reply_text('Ask my Owner to use this Command...')
        return

    try:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text('Incorrect format... Please use: /delete ID')
            return

        character = await collection.find_one_and_delete({'id': args[0]})

        if character:
            await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
            await update.message.reply_text('DONE')
        else:
            await update.message.reply_text('Deleted Successfully from db, but character not found In Channel')
    except Exception as e:
        await update.message.reply_text(f'{str(e)}')

async def update(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)

    if user_id != str(Config.OWNER_ID) and user_id not in [str(uid) for uid in Config.SUDO_USERS]:
        await update.message.reply_text('You do not have permission to use this command.')
        return

    try:
        args = context.args
        if len(args) != 3:
            await update.message.reply_text('Incorrect format. Please use: /update id field new_value')
            return

        character = await collection.find_one({'id': args[0]})
        if not character:
            await update.message.reply_text('Character not found.')
            return

        valid_fields = ['img_url', 'name', 'anime', 'rarity']
        if args[1] not in valid_fields:
            await update.message.reply_text(f'Invalid field. Please use one of the following: {", ".join(valid_fields)}')
            return

        if args[1] in ['name', 'anime']:
            new_value = args[2].replace('-', ' ').title()
        elif args[1] == 'rarity':
            try:
                rarity_number = int(args[2])
            except ValueError:
                await update.message.reply_text('❌ Rarity must be a number between 1-15.')
                return
            
            rarity_level = RarityLevel.get_by_number(rarity_number)
            if not rarity_level:
                await update.message.reply_text('❌ Invalid rarity. Please use 1-15.')
                return
            
            new_value = rarity_level.value[1]
        else:
            new_value = args[2]

        await collection.find_one_and_update({'id': args[0]}, {'$set': {args[1]: new_value}})

        if args[1] == 'img_url':
            await context.bot.delete_message(chat_id=CHARA_CHANNEL_ID, message_id=character['message_id'])
            message = await context.bot.send_photo(
                chat_id=CHARA_CHANNEL_ID,
                photo=new_value,
                caption=f'<b>Character Name:</b> {character["name"]}\n<b>Anime Name:</b> {character["anime"]}\n<b>Rarity:</b> {character["rarity"]}\n<b>ID:</b> {character["id"]}\nUpdated by <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>',
                parse_mode='HTML'
            )
            character['message_id'] = message.message_id
            await collection.find_one_and_update({'id': args[0]}, {'$set': {'message_id': message.message_id}})
        else:
            await context.bot.edit_message_caption(
                chat_id=CHARA_CHANNEL_ID,
                message_id=character['message_id'],
                caption=f'<b>Character Name:</b> {character["name"]}\n<b>Anime Name:</b> {character["anime"]}\n<b>Rarity:</b> {character["rarity"]}\n<b>ID:</b> {character["id"]}\nUpdated by <a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>',
                parse_mode='HTML'
            )

        await update.message.reply_text('Updated Done in Database.... But sometimes it Takes Time to edit Caption in Your Channel..So wait..')
    except Exception as e:
        await update.message.reply_text(f'I guess did not added bot in channel.. or character uploaded Long time ago.. Or character not exits.. orr Wrong id')

UPLOAD_HANDLER = CommandHandler('upload', upload, block=False)
application.add_handler(UPLOAD_HANDLER)
DELETE_HANDLER = CommandHandler('delete', delete, block=False)
application.add_handler(DELETE_HANDLER)
UPDATE_HANDLER = CommandHandler('update', update, block=False)
application.add_handler(UPDATE_HANDLER)