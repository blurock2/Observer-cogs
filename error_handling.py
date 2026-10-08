"""Shared command and UI error boundaries for Observer."""
from __future__ import annotations

import logging

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
              channel_id=None, reference=None) -> str | None:
    original = unwrap(error)
    expected = isinstance(original, (commands.UserInputError, commands.CheckFailure,
                                     commands.CommandOnCooldown, commands.MaxConcurrencyReached,
                                     app_commands.CheckFailure, app_commands.TransformerError,
                                     app_commands.CommandOnCooldown))
    if expected:
        logger.warning("Rejected %s user=%s guild=%s channel=%s reference=%s: %s",
                       source, user_id, guild_id, channel_id, reference, type(original).__name__)
        return None
    else:
        error_id = new_error_id()
        logger.error("Failed %s user=%s guild=%s channel=%s reference=%s",
                     source, user_id, guild_id, channel_id, reference,
                     exc_info=(type(original), original, original.__traceback__),
                     extra={"error_id": error_id, "error_source": source,
                            "error_guild_id": guild_id, "error_user_id": user_id})
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
