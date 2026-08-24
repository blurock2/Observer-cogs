# This single source of truth is imported by cogs that need the owner ID.
# It deliberately does NOT import anything from the bot to avoid circular imports or sum nonhalant shit like that.

BOT_OWNER_ID = 805687087784394773


def is_bot_owner(user) -> bool:
    """True if the given user/member is the configured bot owner."""
    return user is not None and getattr(user, "id", None) == BOT_OWNER_ID