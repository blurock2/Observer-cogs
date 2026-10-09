from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from cogs.config import BOT_OWNER_ID, RESTRICTED_BOT_ID, OBSERVER_SUPPORT_GUILD_ID, is_bot_owner
from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import cleanup_old_backups, migrate_legacy_databases
from logging_config import configure_logging
from error_reporting import ErrorReporter
from error_handling import handle_interaction_error, handle_prefix_error

load_dotenv()
configure_logging()
logger = logging.getLogger("observer.bot")
migrate_legacy_databases(
    Path(__file__).resolve().parent / "data" / "bot.db"
)
cleanup_old_backups()


# ============================================================
# Intents

intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.messages = True
intents.reactions = True
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.presences = True


# ============================================================
# Bot

class RestrictedCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.client.command_access_allowed(interaction.user, interaction.guild):
            return True

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "This bot's commands are limited to configured staff and tester roles.",
                ephemeral=True,
            )
        return False

class MyBot(commands.Bot):
    EXTENSIONS = (
        "cogs.setup_ui",
        "cogs.todo",
        "cogs.afk",
        "cogs.autoroles",
        "cogs.Bookmarks",
        "cogs.temproles",
        "cogs.audit_log",
        "cogs.acc_link",
        "cogs.reaction_roles",
        "cogs.message_quoter",
        "cogs.utilities",
        "cogs.report_msg",
        "cogs.server_stats",
        "cogs.temporary_voice",
        "cogs.rules",
        "cogs.tickets",
        "cogs.reminder",
        "cogs.message_relay",
        "cogs.moderation",
        "cogs.security",
        "cogs.tags",
        "cogs.mentions",
        "cogs.member_commands",
        "cogs.help",
        "cogs.leveling",
        "cogs.weather",
        "cogs.spotify_show",
        "cogs.app_bridge",
    )

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            member_cache_flags=discord.MemberCacheFlags.all(),
            owner_id=BOT_OWNER_ID,
            tree_cls=RestrictedCommandTree,
        )
        self.setup_store = SetupConfigStore(DB_PATH)
        self.error_reporter = ErrorReporter(self, OBSERVER_SUPPORT_GUILD_ID)

        # These are defaults inherited by commands which do not explicitly
        # define their own allowed contexts.
        self.tree.allowed_contexts = app_commands.AppCommandContext(
            guild=True,
            dm_channel=True,
            private_channel=True,
        )

        # Allow both server-installed and user-installed commands.
        self.tree.allowed_installs = app_commands.AppInstallationType(
            guild=True,
            user=True,
        )

    def command_access_allowed(self, user, guild: discord.Guild | None) -> bool:
        if self.user is None or self.user.id != RESTRICTED_BOT_ID:
            return True
        if is_bot_owner(user):
            return True
        if guild is None:
            return False
        restricted_access = self.setup_store.get(
            guild.id, "bot", "restricted_access", default=True
        )
        if isinstance(restricted_access, str):
            restricted_access = restricted_access.strip().lower() not in {
                "false", "0", "no", "off", ""
            }
        if not restricted_access:
            return True

        role_ids: set[int] = set()
        for key in ("staff_role", "tester_role"):
            value = self.setup_store.get(guild.id, "bot", key)
            try:
                if isinstance(value, str):
                    value = value.strip().removeprefix("<@&").removesuffix(">")
                role_ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return any(role.id in role_ids for role in getattr(user, "roles", []))

    async def close(self) -> None:
        await self.error_reporter.close()
        await super().close()

    async def process_commands(self, message: discord.Message) -> None:
        context = await self.get_context(message)
        if not self.command_access_allowed(context.author, context.guild):
            return
        await self.invoke(context)

    async def setup_hook(self) -> None:
        """
        Load all cogs and synchronize the Observer command namespace.
        """

        self.error_reporter.start()

        for extension in self.EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension: %s", extension)

            except Exception:
                logger.exception("Failed to load extension %s", extension)

        try:
            synced_commands = await self.tree.sync()
            logger.info(
                "Synchronized %d Observer application command(s).",
                len(synced_commands),
            )
        except discord.HTTPException:
            logger.exception("Failed to synchronize Observer application commands")


bot = MyBot()


# ============================================================
# Prefix command: reload cogs

@bot.command(name="reload_cogs")
@commands.is_owner()
async def reload_cogs(ctx: commands.Context) -> None:
    """
    Reload all configured extensions and globally sync commands afterward.

    Extensions that are configured but not currently loaded (e.g. they
    failed to load at startup because of a missing dependency) are
    loaded fresh instead of being skipped, so fixing the underlying
    problem and running !test_reload_cogs is enough to bring them up
    without a full bot restart.
    """

    importlib.invalidate_caches()

    extensions = tuple(
        dict.fromkeys(
            (
                *bot.EXTENSIONS,
                *bot.extensions.keys(),
            )
        )
    )

    if not extensions:
        await ctx.send("❌ No cogs are configured or currently loaded.")
        return

    results: list[str] = []

    for extension in extensions:
        if extension not in bot.extensions:
            try:
                await bot.load_extension(extension)

                results.append(f"✅ `{extension}` loaded (was not loaded).")
                logger.info("Loaded extension during reload: %s", extension)

            except Exception as error:
                results.append(
                    f"❌ `{extension}` failed to load: "
                    f"`{type(error).__name__}: {error}`"
                )

                logger.exception("Failed to load extension %s", extension)

            continue

        module = sys.modules.get(extension)
        module_path = getattr(module, "__file__", "unknown file")

        try:
            await bot.reload_extension(extension)

            results.append(f"✅ `{extension}` reloaded.")
            logger.info("Reloaded extension %s from %s", extension, module_path)

        except Exception as error:
            results.append(
                f"❌ `{extension}` failed to reload: "
                f"`{type(error).__name__}: {error}`"
            )

            logger.exception(
                "Failed to reload extension %s from %s",
                extension,
                module_path,
            )

    response = "\n".join(results)

    try:
        synced_commands = await bot.tree.sync()
        response += f"\n✅ Synchronized {len(synced_commands)} application command(s)."
        logger.info("Synchronized %d application command(s) after cog reload", len(synced_commands))
    except Exception as error:
        response += f"\n❌ Application command sync failed: `{type(error).__name__}: {error}`"
        logger.exception("Failed to synchronize application commands after cog reload")

    if len(response) <= 2000:
        await ctx.send(response)
    else:
        for start in range(0, len(response), 1900):
            await ctx.send(response[start:start + 1900])


@bot.command(name="sync_commands")
@commands.is_owner()
async def sync_commands(ctx: commands.Context) -> None:
    """Explicitly synchronize global application commands after changes."""
    try:
        synced_commands = await bot.tree.sync()
        await ctx.send(
            f"🔄 Globally synced {len(synced_commands)} application command(s)."
        )
        logger.info("Globally synced %d application command(s)", len(synced_commands))
    except Exception as error:
        logger.exception("Failed to synchronize application commands")
        await ctx.send(
            "❌ Slash commands did not sync: "
            f"`{type(error).__name__}: {error}`"
        )


# ============================================================
# Prefix command: restart

@bot.command(name="restart")
@commands.is_owner()
async def restart_bot(ctx: commands.Context) -> None:
    """Notify configured channels and restart the bot."""

    from cogs.setup_ui import DB_PATH, SetupConfigStore

    store = SetupConfigStore(DB_PATH)
    notified = 0
    failed = 0

    for guild in bot.guilds:
        log_channel_id = store.get(
            guild.id,
            "bot",
            "log_channel",
        )

        if not log_channel_id:
            continue

        channel = guild.get_channel(int(log_channel_id))

        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            await channel.send(
                "🔄 Bot is restarting… Please wait a moment."
            )
            notified += 1

        except discord.HTTPException:
            failed += 1

    await ctx.send(
        f"Sent restart notice to {notified} log channel(s) "
        f"({failed} failed). Restarting now…"
    )

    await asyncio.sleep(1.5)

    os.execv(
        sys.executable,
        [sys.executable, *sys.argv],
    )


# ============================================================
# Prefix command: inspect commands

@bot.command(name="debug_commands")
@commands.is_owner()
async def debug_commands(ctx: commands.Context) -> None:
    """Display locally registered application commands."""

    local_commands = bot.tree.walk_commands()
    lines = [
        f"Tree default contexts: {bot.tree.allowed_contexts}",
        f"Tree default installs: {bot.tree.allowed_installs}",
        "",
        "Local commands:",
    ]

    for command in local_commands:
        lines.append(
            f"- /{command.qualified_name} "
            f"guild_ids={getattr(command, 'guild_ids', None)}"
        )

    try:
        global_commands = await bot.tree.fetch_commands()

        lines.extend(
            (
                "",
                "Commands fetched from Discord:",
            )
        )

        for command in global_commands:
            lines.append(
                f"- /{command.name} "
                f"id={command.id} "
                f"guild_id={command.guild_id}"
            )

    except discord.HTTPException as error:
        lines.extend(
            (
                "",
                f"Could not fetch global commands: {error}",
            )
        )

    response = "\n".join(lines)

    if len(response) <= 2000:
        await ctx.send(response)
    else:
        for start in range(0, len(response), 1900):
            await ctx.send(response[start:start + 1900])


# ============================================================
# Events

@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        logger.info("Logged in as %s (%s)", bot.user, bot.user.id)

    logger.info("Connected to %d guild(s)", len(bot.guilds))


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    await handle_prefix_error(ctx, error)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    await handle_interaction_error(interaction, error)


# ============================================================
# Main

async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    try:
        async with bot:
            await bot.start(token)

    except asyncio.CancelledError:
        logger.info("Bot shutdown requested")

    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped cleanly")