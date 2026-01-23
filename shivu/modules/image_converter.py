import asyncio
import os
import aiohttp
from typing import Optional

from shivu import collection  # MongoDB characters collection


CATBOX_API = "https://catbox.moe/user/api.php"


async def _download_from_telegram(bot_token: str, file_id: str) -> Optional[bytes]:
    """Download image bytes from Telegram using file_id"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{bot_token}/getFile",
                params={"file_id": file_id},
                timeout=20
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                if not data.get("ok"):
                    return None
                file_path = data["result"]["file_path"]

            async with session.get(
                f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
                timeout=20
            ) as r:
                if r.status == 200:
                    return await r.read()
    except Exception:
        return None

    return None


async def _upload_to_catbox(image_bytes: bytes) -> Optional[str]:
    """Upload image bytes to catbox.moe and return direct image URL"""
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("reqtype", "fileupload")
            data.add_field(
                "fileToUpload",
                image_bytes,
                filename="image.jpg",
                content_type="image/jpeg"
            )

            async with session.post(CATBOX_API, data=data, timeout=30) as r:
                if r.status == 200:
                    url = (await r.text()).strip()
                    if url.startswith("http"):
                        return url
    except Exception:
        return None

    return None


async def convert_file_id_to_url(character_id: str, file_id: str) -> None:
    """
    Background task:
    file_id -> Telegram download -> catbox upload -> DB update
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        return

    image_bytes = await _download_from_telegram(bot_token, file_id)
    if not image_bytes:
        return

    img_url = await _upload_to_catbox(image_bytes)
    if not img_url:
        return

    # Update MongoDB
    await collection.update_one(
        {"id": character_id},
        {"$set": {"img_url": img_url}}
    )


def start_background_conversion(character_id: str, file_id: str) -> None:
    """Fire-and-forget background task"""
    asyncio.create_task(
        convert_file_id_to_url(character_id, file_id)
    )