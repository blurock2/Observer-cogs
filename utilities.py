import socket

import discord
from discord.ext import commands

from cogs.setup_ui import SetupConfigStore, DB_PATH


MODULE_KEY = "utilities"


class Utilities(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(
            self.store.get(guild_id, MODULE_KEY, "enabled", default=False)
        )

    @commands.command()
    @commands.guild_only()
    async def ping(self, ctx: commands.Context) -> None:
        """Show the hosting machine and Discord latency."""
        if ctx.guild is None:
            return

        if not self._is_enabled(ctx.guild.id):
            await ctx.send(
                "The Utilities module is disabled. "
                "Enable it through `/setup` first."
            )
            return

        host_name = socket.gethostname()
        latency_ms = round(self.bot.latency * 1000)

        await ctx.send(
            f"🏓 Pong!\n"
            f"**Host server:** `{host_name}`\n"
            f"**Discord latency:** `{latency_ms} ms`"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utilities(bot))