"""Helpers for notifying members about moderation actions."""

from __future__ import annotations

import logging

import discord
from discord.ui import View

logger = logging.getLogger("observer.moderation")


async def dm_moderation_action(
    user: discord.abc.User,
    guild: discord.Guild,
    action: str,
    reason: str | None,
    duration: str | None = None,
    moderator: discord.abc.User | None = None,
    view: View | None = None,
) -> discord.Message | None:
    """Send a private notice without allowing DM failures to undo moderation."""
    reason_text = (reason or "No reason provided").replace('"', "'")
    action_key = action.casefold()
    # Purges are channel cleanup actions. They belong in staff logs and cases,
    # but should never produce individual user DMs.
    if action_key == "purge" or action_key.startswith("purge:"):
        return None
    if action_key in {"timeout removed", "mute removed"}:
        content = f"Your timeout in {guild.name} has been removed."
    elif action_key in {"ban lifted", "unban"}:
        content = f"Your ban from {guild.name} has been lifted."
    elif action_key in {"warn", "warning"}:
        content = f'You have been Warned in {guild.name} for "{reason_text}"'
    elif action_key in {"ban", "permanent ban", "temporary ban"}:
        content = f'You have been Banned from {guild.name} for "{reason_text}"'
        if duration:
            content += f", for {duration}"
    elif action_key in {"kick"}:
        content = f'You have been Kicked from {guild.name} for "{reason_text}"'
    elif action_key.startswith("timeout") or action_key.startswith("mute"):
        if duration is None and " for " in action:
            duration = action.split(" for ", 1)[1]
        if duration:
            duration = duration.replace("minute(s)", "minutes")
        content = f'You have been Muted in {guild.name} for "{reason_text}"'
        if duration:
            content += f", for {duration}"
    else:
        content = (
            f'A moderation action ({action}) was taken against you in {guild.name} '
            f'for "{reason_text}"'
        )
    if moderator is not None and action_key in {
        "ban", "permanent ban", "temporary ban"
    }:
        content += (
            f"\nModerator: {moderator.mention} ({moderator})"
            "\nYou can contact them on Discord to request an appeal."
        )
    if view is not None and action_key in {"ban", "permanent ban"}:
        content += (
            "\nTo use the in-app appeal, you must share a server with this bot "
            "or add the app to your Discord account."
        )
    send = getattr(user, "send", None)
    if send is None:
        return None
    try:
        return await send(
            content[:1900],
            allowed_mentions=discord.AllowedMentions.none(),
            view=view,
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        logger.info(
            "Could not DM moderation action to user %s in guild %s: %s",
            user.id,
            guild.id,
            error,
        )
        return None
