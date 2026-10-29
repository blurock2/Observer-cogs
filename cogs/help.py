from __future__ import annotations

from error_handling import ErrorHandledView, ObserverCog

from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore

MODULE_KEY = "help"
HELP_COLOR = discord.Color(0x96EDF1)
SUPPORT_URL = "https://discord.gg/tccYb2XSxR"
PREFIX = "!"
COMMAND_NAME_PREFIX = ""


class HelpView(ErrorHandledView):
    def __init__(
        self,
        cog: HelpCog,
        pages: list[tuple[str, str]],
        *,
        navigation: bool = True,
        timeout: float | None = 300.0,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.pages = pages
        self.current_page = 0
        if not navigation:
            # Keep the support button, but omit page controls on a
            # category-only result so it cannot browse unrelated pages.
            children = list(self.children)
            if len(children) >= 3:
                self.remove_item(children[2])
                self.remove_item(children[0])

    def _build_embed(self) -> discord.Embed:
        title, description = self.pages[self.current_page]

        embed = discord.Embed(
            title=title,
            description=description,
            color=HELP_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text=(
                f"Page {self.current_page + 1} of {len(self.pages)} "
                "â€¢ Use the buttons to navigate."
            )
        )
        return embed

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        # The view is deliberately usable by anyone who can see it.
        return True

    @discord.ui.button(
        label="â—€ï¸",
        style=discord.ButtonStyle.secondary,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_page <= 0:
            await interaction.response.send_message(
                "You are already on the first page.",
                ephemeral=True,
            )
            return

        self.current_page -= 1
        await interaction.response.edit_message(
            embed=self._build_embed(),
            view=self,
        )

    @discord.ui.button(
        label="Support Server",
        style=discord.ButtonStyle.blurple,
        emoji="ðŸ›Ÿ",
    )
    async def support_server(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        embed = discord.Embed(
            title="ðŸ›Ÿ Support Server",
            description=(
                f"[Click here to join the support server]({self.cog.support_url})"
            ),
            color=HELP_COLOR,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(
        label="â–¶ï¸",
        style=discord.ButtonStyle.secondary,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.current_page >= len(self.pages) - 1:
            await interaction.response.send_message(
                "You are already on the last page.",
                ephemeral=True,
            )
            return

        self.current_page += 1
        await interaction.response.edit_message(
            embed=self._build_embed(),
            view=self,
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


class HelpCog(ObserverCog):
    """
    Dynamic prefix and slash-command help.

    Dashboard settings:
    - help.enabled
    - help.help_channel
    - help.dm_help
    """

    def __init__(
        self,
        bot: commands.Bot,
        *,
        support_url: str = SUPPORT_URL,
        prefix: str = PREFIX,
    ):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.support_url = support_url
        self.prefix = prefix

    # ============================================================ Setup config

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", default=False))

    def _get_help_channel_id(self, guild_id: int) -> int | None:
        value = self._get(guild_id, "help_channel")

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _dm_help(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "dm_help", default=False))

    # ============================================================ Command discovery

    @staticmethod
    def _format_commands(
        names: list[str],
        per_line: int = 3,
    ) -> str:
        if not names:
            return "No commands available."

        lines = []

        for index in range(0, len(names), per_line):
            chunk = names[index:index + per_line]
            lines.append(" ".join(f"`{name}`" for name in chunk))

        return "\n".join(lines)

    def _prefix_command_names(self) -> list[str]:
        names = []

        for command in self.bot.commands:
            if command.hidden:
                continue

            if command.name == "help":
                names.append(f"{self.prefix}help")
            else:
                names.append(f"{self.prefix}{command.name}")

        return sorted(set(names), key=str.lower)

    def _slash_command_names(self) -> list[str]:
        names = []

        for command in self.bot.tree.get_commands():
            if isinstance(command, app_commands.Command):
                names.append(f"/{command.name}")

        return sorted(set(names), key=str.lower)

    def _commands_by_cog(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)

        for command in self.bot.commands:
            if command.hidden:
                continue

            cog_name = (
                command.cog.qualified_name
                if command.cog is not None
                else "Other"
            )
            grouped[cog_name].append(f"{self.prefix}{command.name}")

        for command in self.bot.tree.get_commands():
            if not isinstance(command, app_commands.Command):
                continue

            cog = command.binding
            cog_name = (
                cog.qualified_name
                if isinstance(cog, commands.Cog)
                else "Other"
            )
            grouped[cog_name].append(f"/{command.name}")

        for commands_list in grouped.values():
            commands_list.sort(key=str.lower)

        return dict(sorted(grouped.items()))

    # ============================================================ Pages

    def _build_pages(self) -> list[tuple[str, str]]:
        intro = (
            "**Welcome!**\n"
            "This bot supports both prefix and slash commands.\n\n"
            f"â€¢ Prefix commands use `{self.prefix}{COMMAND_NAME_PREFIX}`\n"
            "â€¢ Slash commands use `/`\n\n"
            f"Use `{self.prefix}{COMMAND_NAME_PREFIX}help <command>` or "
            "`/help command:<name>` "
            "for prefix-command details."
        )

        pages: list[tuple[str, str]] = [
            (
                "ðŸ¤– Bot Help â€¢ Overview",
                intro,
            ),
            (
                "ðŸ’¬ Bot Help â€¢ Prefix Commands",
                self._format_commands(self._prefix_command_names()),
            ),
            (
                "âš™ï¸ Bot Help â€¢ Slash Commands",
                self._format_commands(self._slash_command_names()),
            ),
        ]

        grouped = self._commands_by_cog()

        for cog_name, command_names in grouped.items():
            pages.append(
                (
                    f"ðŸ“š Bot Help â€¢ {cog_name}"[:256],
                    self._format_commands(command_names, per_line=2),
                )
            )

        return pages

    def _category_page_index(self, category: str, pages: list[tuple[str, str]]) -> int | None:
        normalize = lambda value: "".join(char for char in value.casefold() if char.isalnum())
        key = normalize(category)
        if key.startswith("prefix"):
            return 1
        if key.startswith("slash") or key.startswith("applicationcommand"):
            return 2
        for index, cog_name in enumerate(self._commands_by_cog(), start=3):
            if normalize(cog_name) == key:
                return index if index < len(pages) else None
        return None

    # ============================================================ Command detail

    def _command_help_embed(
        self,
        command: commands.Command,
    ) -> discord.Embed:
        usage = f"{self.prefix}{command.name}"
        signature = command.signature.strip()

        if signature:
            usage += f" {signature}"

        embed = discord.Embed(
            title=f"ðŸ“– Command: `{command.name}`",
            description=f"**Usage**\n`{usage}`",
            color=HELP_COLOR,
            timestamp=discord.utils.utcnow(),
        )

        if command.help:
            embed.add_field(
                name="ðŸ“ Description",
                value=command.help,
                inline=False,
            )

        if command.aliases:
            embed.add_field(
                name="ðŸ” Aliases",
                value=", ".join(
                    f"`{alias}`"
                    for alias in command.aliases
                ),
                inline=False,
            )

        embed.set_footer(
            text=f"Prefix commands use: {self.prefix}{COMMAND_NAME_PREFIX}"
        )
        return embed

    async def _is_allowed_channel(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            return True

        required_channel_id = self._get_help_channel_id(
            interaction.guild.id
        )

        if required_channel_id is None:
            return True

        if interaction.channel_id == required_channel_id:
            return True

        await interaction.response.send_message(
            f"Please use the configured help channel: <#{required_channel_id}>.",
            ephemeral=True,
        )
        return False

    async def _send_slash_help(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        view: discord.ui.View | None = None,
    ) -> None:
        guild = interaction.guild

        if guild is not None and self._dm_help(guild.id):
            try:
                await interaction.user.send(embed=embed, view=view)
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I could not DM you. Please enable DMs from server members.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "I sent the help menu to your DMs.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    # ============================================================ Prefix help

    @commands.command(
        name="help",
        help="Show the bot help menu or details about a prefix command.",
    )
    @commands.guild_only()
    async def help_prefix(
        self,
        ctx: commands.Context,
        *,
        command: str | None = None,
    ) -> None:
        if ctx.guild is None:
            return

        if not self._is_enabled(ctx.guild.id):
            await ctx.send(
                "The Help module is disabled. Enable it through `/setup` first."
            )
            return

        help_channel_id = self._get_help_channel_id(ctx.guild.id)

        if help_channel_id is not None and ctx.channel.id != help_channel_id:
            await ctx.send(
                f"Please use the configured help channel: <#{help_channel_id}>."
            )
            return

        if command:
            command_name = command.lower().lstrip("/!")
            pages = self._build_pages()
            category_page = self._category_page_index(command_name, pages)
            if category_page is not None:
                view = HelpView(self, [pages[category_page]], navigation=False)
                if self._dm_help(ctx.guild.id):
                    try:
                        await ctx.author.send(embed=view._build_embed(), view=view)
                    except discord.Forbidden:
                        await ctx.send("I could not DM you. Please enable DMs from server members.")
                        return
                    await ctx.send("I sent the help menu to your DMs.")
                else:
                    await ctx.send(embed=view._build_embed(), view=view)
                return

            found_command = self.bot.get_command(command_name)

            if found_command is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="âŒ Command Not Found",
                        description=(
                            f"I could not find the prefix command "
                            f"`{command_name}`."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return

            await ctx.send(embed=self._command_help_embed(found_command))
            return

        pages = self._build_pages()
        view = HelpView(self, pages)

        if self._dm_help(ctx.guild.id):
            try:
                await ctx.author.send(
                    embed=view._build_embed(),
                    view=view,
                )
            except discord.Forbidden:
                await ctx.send(
                    "I could not DM you. Please enable DMs from server members."
                )
                return

            await ctx.send("I sent the help menu to your DMs.")
            return

        await ctx.send(embed=view._build_embed(), view=view)

    # ============================================================ Slash help

    @app_commands.command(
        name="help",
        description="Show the bot help menu or prefix-command details.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        command="Optional prefix command to get details about.",
    )
    async def help_slash(
        self,
        interaction: discord.Interaction,
        command: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not self._is_enabled(interaction.guild.id):
            await interaction.response.send_message(
                "The Help module is disabled. Enable it through `/setup` first.",
                ephemeral=True,
            )
            return

        if not await self._is_allowed_channel(interaction):
            return

        if command:
            command_name = command.lower().lstrip("/!")
            found_command = self.bot.get_command(command_name)

            if found_command is None:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="âŒ Command Not Found",
                        description=(
                            f"I could not find the prefix command "
                            f"`{command_name}`."
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await self._send_slash_help(
                interaction,
                embed=self._command_help_embed(found_command),
            )
            return

        pages = self._build_pages()
        view = HelpView(self, pages)

        await self._send_slash_help(
            interaction,
            embed=view._build_embed(),
            view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))

