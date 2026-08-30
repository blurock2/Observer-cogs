from __future__ import annotations

import asyncio
import importlib
import os
import sys
import traceback

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()


# ============================================================
# Owner override

BOT_OWNER_ID = 805687087784394773


def is_bot_owner(user: discord.abc.User | None) -> bool:
    """Return whether the user is the configured bot owner."""
    return user is not None and user.id == BOT_OWNER_ID


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

class MyBot(commands.Bot):
    EXTENSIONS = (
        "cogs.setup_ui",
        "cogs.acc_link",
        "cogs.reaction_roles",
        "cogs.message_quoter",
        "cogs.utilities",
        "cogs.report_msg",
        "cogs.server_stats",
        "cogs.temporary_voice",
        "cogs.rules",
        "cogs.tickets",
        "cogs.message_relay",
        "cogs.moderation",
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
        )

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

    async def setup_hook(self) -> None:
        """
        Load all cogs, then globally synchronize the application command tree.
        """

        for extension in self.EXTENSIONS:
            try:
                await self.load_extension(extension)
                print(f"Loaded extension: {extension}")

            except Exception as error:
                print(
                    f"Failed to load extension {extension}: "
                    f"{type(error).__name__}: {error}"
                )
                traceback.print_exc()

        print("\nLocal application commands before sync:")

        for command in self.tree.walk_commands():
            print(
                f"  /{command.qualified_name} "
                f"({type(command).__name__})"
            )

        try:
            synced_commands = await self.tree.sync()

            print(
                f"\nGlobally synced "
                f"{len(synced_commands)} application command(s)."
            )

            print("Commands returned by Discord:")

            for command in synced_commands:
                print(f"  /{command.name}")

        except Exception as error:
            print(
                "Failed to synchronize application commands: "
                f"{type(error).__name__}: {error}"
            )
            traceback.print_exc()


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
    problem and running !reload_cogs is enough to bring them up
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
                print(f"Loaded extension: {extension}")

            except Exception as error:
                results.append(
                    f"❌ `{extension}` failed to load: "
                    f"`{type(error).__name__}: {error}`"
                )

                print(
                    f"Failed to load extension {extension}: "
                    f"{type(error).__name__}: {error}"
                )
                traceback.print_exc()

            continue

        module = sys.modules.get(extension)
        module_path = getattr(module, "__file__", "unknown file")

        try:
            await bot.reload_extension(extension)

            results.append(f"✅ `{extension}` reloaded.")
            print(
                f"Reloaded extension: {extension}\n"
                f"File: {module_path}"
            )

        except Exception as error:
            results.append(
                f"❌ `{extension}` failed to reload: "
                f"`{type(error).__name__}: {error}`"
            )

            print(
                f"Failed to reload extension {extension}: "
                f"{type(error).__name__}: {error}"
            )
            print(f"File: {module_path}")
            traceback.print_exc()

    try:
        synced_commands = await bot.tree.sync()

        results.append(
            f"\n🔄 Globally synced "
            f"{len(synced_commands)} application command(s)."
        )

        print(
            "Globally synced "
            f"{len(synced_commands)} application command(s) after reload."
        )

    except Exception as error:
        results.append(
            "\n❌ Slash commands did not sync: "
            f"`{type(error).__name__}: {error}`"
        )

        print(
            "Failed to synchronize application commands after reload:"
        )
        traceback.print_exc()

    response = "\n".join(results)

    if len(response) <= 2000:
        await ctx.send(response)
    else:
        for start in range(0, len(response), 1900):
            await ctx.send(response[start:start + 1900])


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
        print(f"Logged in as {bot.user} ({bot.user.id})")

    print(f"Connected to {len(bot.guilds)} guild(s).")


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """Handle prefix-command errors."""

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.NotOwner):
        await ctx.send("Only the bot owner can use that command.")
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use that command.")
        return

    if isinstance(error, commands.BotMissingPermissions):
        await ctx.send(
            "The bot is missing the required permissions for that command."
        )
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("You are missing a required argument.")
        return

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send(
            "This command can only be used inside a server."
        )
        return

    if isinstance(error, commands.CommandInvokeError):
        original_error = error.original

        print("Error while running a prefix command:")
        traceback.print_exception(
            type(original_error),
            original_error,
            original_error.__traceback__,
        )

        await ctx.send(
            "The command encountered an error. "
            "Check the bot console for details."
        )
        return

    print("Unhandled prefix-command error:")
    traceback.print_exception(
        type(error),
        error,
        error.__traceback__,
    )


@bot.event
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    """Handle slash-command errors."""

    if isinstance(error, app_commands.BotMissingPermissions):
        missing = getattr(error, "missing_permissions", None)

        if missing and not is_bot_owner(interaction.user):
            permission_names = ", ".join(
                permission.replace("_", " ").title()
                for permission in missing
            )
            message = (
                "I am missing these Discord permissions: "
                f"{permission_names}."
            )
        else:
            message = (
                "I am missing the required Discord permissions "
                "for that command."
            )

    elif isinstance(error, app_commands.MissingPermissions):
        if is_bot_owner(interaction.user):
            message = (
                "Command check failed. "
                "If this persists, check the bot console."
            )
        else:
            missing = getattr(error, "missing_permissions", None)

            if missing:
                permission_names = ", ".join(
                    permission.replace("_", " ").title()
                    for permission in missing
                )
                message = (
                    "You are missing these permissions: "
                    f"{permission_names}."
                )
            else:
                message = (
                    "You are missing the required permissions "
                    "for that command."
                )

    elif isinstance(error, app_commands.CheckFailure):
        message = (
            "You need the configured moderator or head-moderator "
            "role to use this command."
        )

    else:
        print("Error while running an application command:")
        traceback.print_exception(
            type(error),
            error,
            error.__traceback__,
        )

        message = (
            "An error occurred while running that slash command. "
            "Check the bot console for details."
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

    except discord.HTTPException:
        pass


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
        print("Bot shutdown requested.")

    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("Bot stopped cleanly.")