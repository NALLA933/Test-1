import os
import sys
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _get_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _get_int_list(name: str, default: str = "") -> List[int]:
    raw_value = os.getenv(name, default)
    values: List[int] = []
    for item in raw_value.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        if not cleaned.lstrip("-").isdigit():
            raise ValueError(f"{name} must contain only comma-separated integers")
        values.append(int(cleaned))
    return values


def _get_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Base configuration class for the Telegram bot."""

    LOGGER: bool = True

    TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "Senpai_Waifu_Grabbing_Bot")

    API_ID: Optional[int] = _get_optional_int("API_ID")
    API_HASH: str = os.getenv("API_HASH", "").strip()

    OWNER_ID: Optional[int] = _get_optional_int("OWNER_ID")
    SUDO_USERS: List[int] = _get_int_list("SUDO_USERS")
    EVAL_USERS: List[int] = _get_int_list("EVAL_USERS")

    GROUP_ID: Optional[int] = _get_optional_int("GROUP_ID")
    CHARA_CHANNEL_ID: Optional[int] = _get_optional_int("CHARA_CHANNEL_ID")

    MONGO_URL: str = os.getenv("MONGO_URL", "").strip()

    IMGBB_API_KEY: str = os.getenv("IMGBB_API_KEY", "").strip()
    BACKUP_CHAT_ID: Optional[int] = _get_optional_int("BACKUP_CHAT_ID")
    ENABLE_AUTO_BACKUP: bool = _get_bool("ENABLE_AUTO_BACKUP", False)

    VIDEO_URL: List[str] = [
        url.strip()
        for url in os.getenv(
            "VIDEO_URL",
            "https://files.catbox.moe/iqeaeb.mp4,https://files.catbox.moe/fp7m2d.mp4,https://files.catbox.moe/cv8r9i.mp4,https://files.catbox.moe/kz2usa.mp4,https://files.catbox.moe/u3gfz5.mp4,https://files.catbox.moe/4w63xt.mp4,https://files.catbox.moe/3mv64w.mp4,https://files.catbox.moe/n2m9av.mp4,https://files.catbox.moe/lrjr1o.mp4,https://files.catbox.moe/xdmuzm.mp4,https://files.catbox.moe/lqsdnr.mp4,https://files.catbox.moe/3mv64w.mp4",
        ).split(",")
        if url.strip()
    ]

    SUPPORT_CHAT: str = os.getenv("SUPPORT_CHAT", "THE_DRAGON_SUPPORT")
    UPDATE_CHAT: str = os.getenv("UPDATE_CHAT", "Senpai_Updates")

    @classmethod
    def validate(cls) -> None:
        errors = []

        if not cls.TOKEN:
            errors.append("BOT_TOKEN is required")

        if not cls.API_ID:
            errors.append("API_ID is required")

        if not cls.API_HASH:
            errors.append("API_HASH is required")

        if not cls.OWNER_ID:
            errors.append("OWNER_ID is required")

        if not cls.MONGO_URL:
            errors.append("MONGO_URL is required")

        if not cls.GROUP_ID:
            errors.append("GROUP_ID is required")

        if not cls.CHARA_CHANNEL_ID:
            errors.append("CHARA_CHANNEL_ID is required")

        if errors:
            print("Configuration Error(s):")
            for error in errors:
                print(f"   - {error}")
            print("\nPlease set the required environment variables and try again.")
            sys.exit(1)

        if cls.OWNER_ID not in cls.SUDO_USERS:
            cls.SUDO_USERS.append(cls.OWNER_ID)

        if not cls.EVAL_USERS:
            cls.EVAL_USERS = [cls.OWNER_ID]
        elif cls.OWNER_ID not in cls.EVAL_USERS:
            cls.EVAL_USERS.append(cls.OWNER_ID)


class Production(Config):
    LOGGER: bool = True


class Development(Config):
    LOGGER: bool = True


Config.validate()
