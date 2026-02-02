import os
import sys
from typing import List


class Config:
    """Base configuration class for the Telegram bot."""

    # Logging
    LOGGER: bool = True

    # Bot Credentials (from BotFather)
    TOKEN: str = os.getenv("BOT_TOKEN", "8551975632:AAHB_mZTANp_yu-eKKyopgergkQJWJ4RYuo")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "Senpai_Waifu_Grabbing_Bot")

    # Telegram API Credentials (from my.telegram.org/apps)
    API_ID: int = int(os.getenv("API_ID", "35660683"))
    API_HASH: str = os.getenv("API_HASH", "7afb42cd73fb5f3501062ffa6a1f87f7")

    # Owner and Sudo Users
    OWNER_ID: int = int(os.getenv("OWNER_ID", "7818323042"))
    SUDO_USERS: List[int] = [
        int(user_id.strip())
        for user_id in os.getenv("SUDO_USERS", "7818323042,8453236527").split(",")
        if user_id.strip().isdigit()
    ]

    # Group and Channel IDs
    GROUP_ID: int = int(os.getenv("GROUP_ID", "-1003727098465"))
    
    # IMPORTANT: Make sure your bot is admin in this channel with "Post Messages" permission
    CHARA_CHANNEL_ID: int = int(os.getenv("CHARA_CHANNEL_ID", "-1003749495721"))

    # Database
    MONGO_URL: str = os.getenv(
        "MONGO_URL",
        "mongodb+srv://ravi:ravi12345@cluster0.hndinhj.mongodb.net/?retryWrites=true&w=majority"
    )

    # Media
    VIDEO_URL: List[str] = [
        url.strip()
        for url in os.getenv(
            "VIDEO_URL",
            "https://files.catbox.moe/iqeaeb.mp4,https://files.catbox.moe/fp7m2d.mp4,https://files.catbox.moe/cv8r9i.mp4,https://files.catbox.moe/kz2usa.mp4,https://files.catbox.moe/u3gfz5.mp4,https://files.catbox.moe/4w63xt.mp4,https://files.catbox.moe/3mv64w.mp4,https://files.catbox.moe/n2m9av.mp4,https://files.catbox.moe/lrjr1o.mp4,https://files.catbox.moe/xdmuzm.mp4,https://files.catbox.moe/lqsdnr.mp4,https://files.catbox.moe/3mv64w.mp4"
        ).split(",")
        if url.strip()
    ]

    # Community Links
    SUPPORT_CHAT: str = os.getenv("SUPPORT_CHAT", "THE_DRAGON_SUPPORT")
    UPDATE_CHAT: str = os.getenv("UPDATE_CHAT", "PICK_X_UPDATE")

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration values."""
        errors = []
        warnings = []

        if not cls.TOKEN:
            errors.append("BOT_TOKEN is required")

        if not cls.API_ID or cls.API_ID == 0:
            errors.append("API_ID is required")

        if not cls.API_HASH:
            errors.append("API_HASH is required")

        if not cls.OWNER_ID or cls.OWNER_ID == 0:
            errors.append("OWNER_ID is required")

        if not cls.MONGO_URL:
            errors.append("MONGO_URL is required")

        if not cls.GROUP_ID or cls.GROUP_ID == 0:
            errors.append("GROUP_ID is required")

        if not cls.CHARA_CHANNEL_ID or cls.CHARA_CHANNEL_ID == 0:
            errors.append("CHARA_CHANNEL_ID is required")
        
        # Validate Channel ID format
        chara_id_str = str(cls.CHARA_CHANNEL_ID)
        if not chara_id_str.startswith("-100"):
            warnings.append(f"CHARA_CHANNEL_ID ({cls.CHARA_CHANNEL_ID}) format seems incorrect. Should start with -100")
        
        # Check if ID length is correct (typically 13 digits: -100 + 10 digits)
        if len(chara_id_str) != 14:  # Including the minus sign
            warnings.append(f"CHARA_CHANNEL_ID length is {len(chara_id_str)}, expected 14 characters (including minus sign)")

        if errors:
            print("❌ Configuration Error(s):")
            for error in errors:
                print(f"   - {error}")
            print("\n💡 Please set the required environment variables and try again.")
            sys.exit(1)
        
        if warnings:
            print("⚠️  Configuration Warning(s):")
            for warning in warnings:
                print(f"   - {warning}")
            print("\n💡 IMPORTANT: Make sure your bot is added as ADMIN in the channel!")
            print(f"   Channel ID: {cls.CHARA_CHANNEL_ID}")
            print("   Required permissions: Post Messages, Edit Messages")
            print()

        # Add OWNER_ID to SUDO_USERS if not already present
        if cls.OWNER_ID not in cls.SUDO_USERS:
            cls.SUDO_USERS.append(cls.OWNER_ID)


class Production(Config):
    """Production environment configuration."""
    LOGGER: bool = True


class Development(Config):
    """Development environment configuration."""
    LOGGER: bool = True


# Auto-validate configuration on import
Config.validate()
