"""Shared command and UI error boundaries for Observer."""
from __future__ import annotations

import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from error_reporting import new_error_id

logger = logging.getLogger("observer.errors")


def unwrap(error: Exception) -> Exception:
    while isinstance(error, (commands.CommandInvokeError, commands.HybridCommandError,
                             app_commands.CommandInvokeError)):
        error = error.original
    return error


def error_message(error: Exception) -> str:
    error = unwrap(error)
    if isinstance(error, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
        names = ", ".join(p.replace("_", " ") for p in error.missing_permissions)
        return f"I am missing these Discord permissions: {names}."
    if isinstance(error, (commands.MissingPermissions, app_commands.MissingPermissions)):
        names = ", ".join(p.replace("_", " ") for p in error.missing_permissions)
        return f"You need these permissions to use this command: {names}."
    if isinstance(error, commands.NotOwner):
        return "Only the bot owner can use that command."
    if isinstance(error, (commands.NoPrivateMessage, app_commands.NoPrivateMessage)):
        return "This command can only be used inside a server."
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return f"Please try again in {error.retry_after:.1f} seconds."
    if isinstance(error, commands.MaxConcurrencyReached):
        return "This command is already running. Please wait before trying again."
    if isinstance(error, commands.MissingRequiredArgument):
        return f"You are missing the required argument: {error.param.name}."
    if isinstance(error, (commands.UserInputError, app_commands.TransformerError)):
        return "I could not read one of the arguments. Check the command's help and try again."
    if isinstance(error, (commands.CheckFailure, app_commands.CheckFailure)):
        return "You cannot use this command here, or you do not have the required role."
    if isinstance(error, discord.Forbidden):
        return "Discord denied this action. Check my permissions and role position."
    if isinstance(error, discord.NotFound):
        return "The requested Discord message, channel, or member is no longer available."
    return "Something went wrong. The error has been logged; please contact the bot owner."


def log_error(error: Exception, *, source: str, user_id=None, guild_id=None,
              channel_id=None, reference=None, event_name=None) -> str | None:
    original = unwrap(error)
    expected = isinstance(original, (commands.UserInputError, commands.CheckFailure,
                                     commands.CommandOnCooldown, commands.MaxConcurrencyReached,
                                     app_commands.CheckFailure, app_commands.TransformerError,
                                     app_commands.CommandOnCooldown))
    if expected:
        # Expected user-side rejections are not runtime warnings and should not
        # flood the support error channel.
        logger.info("Rejected %s user=%s guild=%s channel=%s reference=%s: %s",
                       source, user_id, guild_id, channel_id, reference, type(original).__name__)
        return None
    else:
        error_id = new_error_id()
        logger.error("Failed %s user=%s guild=%s channel=%s reference=%s",
                     source, user_id, guild_id, channel_id, reference,
                     exc_info=(type(original), original, original.__traceback__),
                     extra={"error_id": error_id, "error_source": source,
                            "error_guild_id": guild_id, "error_user_id": user_id,
                            "error_channel_id": channel_id, "error_event_name": event_name})
        return error_id


async def handle_interaction_error(interaction: discord.Interaction, error: Exception,
                                   *, source: str | None = None) -> None:
    # Command-local, cog and tree handlers can all be invoked for the same failure.
    if interaction.extras.get("observer_error_handled"):
        return
    interaction.extras["observer_error_handled"] = True
    error_id = log_error(error, source=source or getattr(interaction.command, "qualified_name", "interaction"),
              user_id=interaction.user.id, guild_id=interaction.guild_id,
              channel_id=interaction.channel_id, reference=interaction.id)
    message = error_message(error)
    if error_id:
        interaction.extras["observer_error_id"] = error_id
        message += f"\nError ID: `{error_id}` — share this with Observer Support."
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(message, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.exception("Could not report interaction error reference=%s", interaction.id)


async def handle_prefix_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if ctx.interaction is not None:
        await handle_interaction_error(ctx.interaction, error)
        return
    if getattr(ctx, "observer_error_handled", False):
        return
    ctx.observer_error_handled = True
    error_id = log_error(error, source=getattr(ctx.command, "qualified_name", "prefix command"),
              user_id=ctx.author.id, guild_id=getattr(ctx.guild, "id", None),
              channel_id=ctx.channel.id, reference=ctx.message.id)
    message = error_message(error)
    if error_id:
        message += f"\nError ID: `{error_id}` — share this with Observer Support."
    try:
        await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.exception("Could not report prefix error message=%s", ctx.message.id)


class ObserverCog(commands.Cog):
    async def cog_app_command_error(self, interaction, error):
        await handle_interaction_error(interaction, error)

    async def cog_command_error(self, ctx, error):
        await handle_prefix_error(ctx, error)


class ErrorHandledView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await handle_interaction_error(interaction, error,
                                       source=f"{type(self).__name__}.{type(item).__name__}")


class ErrorHandledModal(discord.ui.Modal):
    async def on_error(self, interaction, error):
        await handle_interaction_error(interaction, error, source=type(self).__name__)


def handle_event_error(bot: commands.Bot, event_name: str, *args, **kwargs) -> None:
    """Report an uncaught Discord event/listener exception with its event arguments."""
    error = sys.exc_info()[1]
    if error is None:
        logger.error("Discord event %s failed without an active exception", event_name)
        return

    guild = user = channel = message = None
    guild_id = user_id = channel_id = reference = None

    # Discord event callbacks receive the objects needed to identify their context.
    for value in (*args, *kwargs.values()):
        if isinstance(value, discord.Interaction):
            guild = value.guild or guild
            user = value.user or user
            channel = value.channel or channel
            reference = value.id or reference
        elif isinstance(value, discord.Message):
            guild = value.guild or guild
            user = value.author or user
            channel = value.channel or channel
            reference = value.id or reference
        elif isinstance(value, discord.Member):
            guild = value.guild or guild
            user = value
        elif isinstance(value, discord.User):
            user = value
        elif isinstance(value, discord.Guild):
            guild = value
        elif isinstance(value, discord.abc.GuildChannel):
            channel = value
            guild = value.guild or guild
        else:
            # Raw gateway payloads expose IDs rather than cached Discord models.
            guild_id = getattr(value, "guild_id", None) or guild_id
            user_id = getattr(value, "user_id", None) or user_id
            channel_id = getattr(value, "channel_id", None) or channel_id
            reference = (getattr(value, "message_id", None)
                         or getattr(value, "id", None) or reference)
            nested_message = getattr(value, "message", None)
            if isinstance(nested_message, discord.Message):
                message = nested_message
                guild = message.guild or guild
                user = message.author or user
                channel = message.channel or channel

    guild_id = getattr(guild, "id", None) or guild_id
    user_id = getattr(user, "id", None) or user_id
    channel_id = getattr(channel, "id", None) or channel_id

    if guild is None and guild_id is not None:
        guild = bot.get_guild(int(guild_id))
    if channel is None and channel_id is not None:
        channel = bot.get_channel(int(channel_id))
        if channel is None and guild is not None:
            channel = guild.get_channel(int(channel_id))
    if user is None and user_id is not None:
        user = bot.get_user(int(user_id))
        if user is None and guild is not None:
            user = guild.get_member(int(user_id))

    log_error(
        error,
        source=f"discord event {event_name}",
        user_id=getattr(user, "id", user_id),
        guild_id=getattr(guild, "id", guild_id),
        channel_id=getattr(channel, "id", channel_id),
        reference=reference,
        event_name=event_name,
    )
