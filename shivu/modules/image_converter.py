import asyncio
import os
import aiohttp
from typing import Optional
from datetime import datetime

# Try to import database module (silent if unavailable for testing)
try:
    from ..database import get_db, Character
except ImportError:
    # Mock for standalone testing
    Character = None
    get_db = None


class ImageConverter:
    """Async background utility for converting Telegram images to catbox.moe"""
    
    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize ImageConverter.
        
        Args:
            bot_token: Telegram bot token (optional, will try to get from env)
        """
        self.bot_token = bot_token or os.getenv('BOT_TOKEN')
        self.session: Optional[aiohttp.ClientSession] = None
        self.timeout = aiohttp.ClientTimeout(total=30)
        
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
            
    async def download_telegram_image(self, file_id: str) -> Optional[bytes]:
        """
        Download image from Telegram using file_id.
        
        Args:
            file_id: Telegram file_id
            
        Returns:
            Image bytes or None if download fails
        """
        if not self.bot_token or not self.session:
            return None
            
        try:
            # Get file path from Telegram
            async with self.session.get(
                f'https://api.telegram.org/bot{self.bot_token}/getFile',
                params={'file_id': file_id}
            ) as response:
                if response.status != 200:
                    return None
                    
                data = await response.json()
                if not data.get('ok'):
                    return None
                    
                file_path = data['result']['file_path']
                
            # Download actual file
            async with self.session.get(
                f'https://api.telegram.org/file/bot{self.bot_token}/{file_path}'
            ) as response:
                if response.status == 200:
                    return await response.read()
                    
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError):
            return None
            
        return None
        
    async def upload_to_catbox(self, image_data: bytes) -> Optional[str]:
        """
        Upload image to catbox.moe and get direct URL.
        
        Args:
            image_data: Raw image bytes
            
        Returns:
            Direct image URL or None if upload fails
        """
        if not self.session:
            return None
            
        try:
            form_data = aiohttp.FormData()
            form_data.add_field('reqtype', 'fileupload')
            form_data.add_field('fileToUpload', 
                              image_data,
                              filename='image.jpg',
                              content_type='image/jpeg')
            
            async with self.session.post(
                'https://catbox.moe/user/api.php',
                data=form_data
            ) as response:
                if response.status == 200:
                    url = (await response.text()).strip()
                    # Validate URL
                    if url and url.startswith('http'):
                        return url
                        
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
            return None
            
        return None
        
    async def update_character_image(self, character_id: str, image_url: str) -> bool:
        """
        Update character's image URL in database.
        
        Args:
            character_id: Character ID
            image_url: New image URL
            
        Returns:
            True if update successful, False otherwise
        """
        if not Character or not get_db:
            return False
            
        try:
            # Get database session
            db_gen = get_db()
            db = await anext(db_gen)
            
            # Find and update character
            character = await db.get(Character, character_id)
            if character:
                character.img_url = image_url
                character.updated_at = datetime.utcnow()
                await db.commit()
                return True
                
        except Exception:
            return False
        finally:
            try:
                await anext(db_gen)  # Close the async generator
            except StopAsyncIteration:
                pass
                
        return False
        
    async def convert_and_update(self, character_id: str, file_id: str) -> bool:
        """
        Main method: Download, upload, and update image URL.
        
        Args:
            character_id: Character ID
            file_id: Telegram file_id
            
        Returns:
            True if all operations successful, False otherwise
        """
        # Download from Telegram
        image_data = await self.download_telegram_image(file_id)
        if not image_data:
            return False
            
        # Upload to catbox.moe
        image_url = await self.upload_to_catbox(image_data)
        if not image_url:
            return False
            
        # Update database
        return await self.update_character_image(character_id, image_url)
        

async def convert_image_background(character_id: str, file_id: str, 
                                  bot_token: Optional[str] = None) -> None:
    """
    Background task function for fire-and-forget image conversion.
    
    Args:
        character_id: Character ID
        file_id: Telegram file_id
        bot_token: Optional bot token (uses env var if not provided)
    """
    async with ImageConverter(bot_token) as converter:
        await converter.convert_and_update(character_id, file_id)


# Convenience function for synchronous contexts
def start_image_conversion(character_id: str, file_id: str, 
                          bot_token: Optional[str] = None) -> None:
    """
    Start image conversion in background (fire-and-forget).
    
    Args:
        character_id: Character ID
        file_id: Telegram file_id
        bot_token: Optional bot token
    """
    asyncio.create_task(
        convert_image_background(character_id, file_id, bot_token)
    )