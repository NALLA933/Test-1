from shivu.config import Config


def is_owner(user_id: int) -> bool:
    return user_id == Config.OWNER_ID


def is_owner_or_sudo(user_id: int) -> bool:
    return is_owner(user_id) or user_id in Config.SUDO_USERS


def can_use_eval(user_id: int) -> bool:
    return user_id in Config.EVAL_USERS
