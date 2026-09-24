import os

# This is the single source of truth for owner configuration.
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "805687087784394773"))
RESTRICTED_BOT_ID = 1533804830479355935


def is_bot_owner(user) -> bool:
    """True if the given user/member is the configured bot owner."""
    return user is not None and getattr(user, "id", None) == BOT_OWNER_ID