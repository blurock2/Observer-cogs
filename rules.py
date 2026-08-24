from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import (
    DB_PATH,
    SetupConfigStore,
    owner_or_has_permissions,
)


MODULE_KEY = "rules"
EMBED_COLOR = 0x96EDF1
VALID_FORMATS = {"embed", "text"}


@dataclass
class Rule:
    rule_id: int
    guild_id: int
    title: str
    content: str
    send_format: str


class RulesStore:
    """Stores configurable rules separately from general setup values."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_table()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_rules (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    send_format TEXT NOT NULL DEFAULT 'embed'
                )
                """
            )

    def add(
        self,
        guild_id: int,
        title: str,
        content: str,
        send_format: str,
    ) -> Rule:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO guild_rules (
                    guild_id,
                    title,
                    content,
                    send_format
                )
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, title, content, send_format),
            )
            rule_id = cursor.lastrowid

        return Rule(
            rule_id=int(rule_id),
            guild_id=guild_id,
            title=title,
            content=content,
            send_format=send_format,
        )

    def get(
        self,
        guild_id: int,
        rule_id: int,
    ) -> Optional[Rule]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT rule_id, guild_id, title, content, send_format
                FROM guild_rules
                WHERE guild_id = ? AND rule_id = ?
                """,
                (guild_id, rule_id),
            ).fetchone()

        return self._row_to_rule(row)

    def list(
        self,
        guild_id: int,
        *,
        query: str = "",
        limit: int = 25,
    ) -> list[Rule]:
        search = f"%{query.strip()}%"

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT rule_id, guild_id, title, content, send_format
                FROM guild_rules
                WHERE guild_id = ?
                  AND (title LIKE ? OR CAST(rule_id AS TEXT) LIKE ?)
                ORDER BY rule_id ASC
                LIMIT ?
                """,
                (guild_id, search, search, limit),
            ).fetchall()

        return [
            rule
            for row in rows
            if (rule := self._row_to_rule(row)) is not None
        ]

    def update(
        self,
        guild_id: int,
        rule_id: int,
        title: str,
        content: str,
        send_format: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE guild_rules
                SET title = ?, content = ?, send_format = ?
                WHERE guild_id = ? AND rule_id = ?
                """,
                (
                    title,
                    content,
                    send_format,
                    guild_id,
                    rule_id,
                ),
            )

        return cursor.rowcount > 0

    def delete(
        self,
        guild_id: int,
        rule_id: int,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM guild_rules
                WHERE guild_id = ? AND rule_id = ?
                """,
                (guild_id, rule_id),
            )

        return cursor.rowcount > 0

    @staticmethod
    def _row_to_rule(row: Optional[sqlite3.Row]) -> Optional[Rule]:
        if row is None:
            return None

        return Rule(
            rule_id=int(row["rule_id"]),
            guild_id=int(row["guild_id"]),
            title=str(row["title"]),
            content=str(row["content"]),
            send_format=str(row["send_format"]),
        )


class RuleModal(discord.ui.Modal):
    """Modal used for both adding and editing a rule."""

    def __init__(
        self,
        cog: "RulesCommand",
        *,
        rule: Optional[Rule] = None,
    ):
        self.cog = cog
        self.rule = rule

        title = "Edit Rule" if rule is not None else "Add Rule"
        super().__init__(title=title)

        self.rule_title = discord.ui.TextInput(
            label="Rule title",
            placeholder="Example: Be respectful",
            default=rule.title if rule else None,
            min_length=1,
            max_length=256,
            required=True,
        )

        self.rule_content = discord.ui.TextInput(
            label="Rule content",
            placeholder="Write the full rule text here...",
            default=rule.content if rule else None,
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=4000,
            required=True,
        )

        self.rule_format = discord.ui.TextInput(
            label="Format: embed or text",
            placeholder="embed",
            default=rule.send_format if rule else "embed",
            min_length=4,
            max_length=5,
            required=True,
        )

        self.add_item(self.rule_title)
        self.add_item(self.rule_content)
        self.add_item(self.rule_format)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "This can only be used inside a server.",
                ephemeral=True,
            )
            return

        title = self.rule_title.value.strip()
        content = self.rule_content.value.strip()
        send_format = self.rule_format.value.strip().lower()

        if send_format not in VALID_FORMATS:
            await interaction.response.send_message(
                "Format must be either `embed` or `text`.",
                ephemeral=True,
            )
            return

        if self.rule is None:
            created_rule = self.cog.rules_store.add(
                guild.id,
                title,
                content,
                send_format,
            )

            await interaction.response.send_message(
                f"Added rule **{created_rule.title}** "
                f"as `{created_rule.send_format}`.",
                ephemeral=True,
            )
            return

        updated = self.cog.rules_store.update(
            guild.id,
            self.rule.rule_id,
            title,
            content,
            send_format,
        )

        if not updated:
            await interaction.response.send_message(
                "That rule no longer exists.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Updated rule **{title}**.",
            ephemeral=True,
        )


class RulesCommand(commands.Cog):
    """
    Per-server configurable rules.

    Dashboard settings:
    - rules.enabled
    - rules.rules_channel
    - rules.member_role
    - rules.react_to_accept
    - rules.default_format

    Rule records are stored in the guild_rules SQLite table.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.setup_store = SetupConfigStore(DB_PATH)
        self.rules_store = RulesStore(DB_PATH)

    # ============================================================ Setup helpers

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(
            self.setup_store.get(
                guild_id,
                MODULE_KEY,
                "enabled",
                default=False,
            )
        )

    def _default_format(self, guild_id: int) -> str:
        value = self.setup_store.get(
            guild_id,
            MODULE_KEY,
            "default_format",
            default="embed",
        )

        value = str(value).strip().lower()

        return value if value in VALID_FORMATS else "embed"

    async def _require_enabled(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return False

        if self._is_enabled(interaction.guild.id):
            return True

        await interaction.response.send_message(
            "The Rules module is disabled. Enable it through `/setup` first.",
            ephemeral=True,
        )
        return False

    @staticmethod
    def _parse_rule_id(value: str) -> Optional[int]:
        value = value.strip()

        if value.startswith("#"):
            value = value[1:]

        first_part = value.split(" ", maxsplit=1)[0]

        try:
            return int(first_part)
        except ValueError:
            return None

    async def rule_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []

        rules = self.rules_store.list(
            interaction.guild.id,
            query=current,
            limit=25,
        )

        return [
            app_commands.Choice(
                name=f"{rule.title}"[:100],
                value=str(rule.rule_id),
            )
            for rule in rules
        ]

    # ============================================================ Rule management

    @app_commands.command(
        name="rule_add",
        description="Add a configurable server rule.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def rule_add(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_enabled(interaction):
            return

        rule = Rule(
            rule_id=0,
            guild_id=interaction.guild.id,
            title="",
            content="",
            send_format=self._default_format(interaction.guild.id),
        )

        await interaction.response.send_modal(
            RuleModal(self, rule=None)
        )

    @app_commands.command(
        name="rule_edit",
        description="Edit a configured server rule.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    @app_commands.describe(rule="Rule to edit")
    @app_commands.autocomplete(rule=rule_autocomplete)
    async def rule_edit(
        self,
        interaction: discord.Interaction,
        rule: str,
    ) -> None:
        if not await self._require_enabled(interaction):
            return

        rule_id = self._parse_rule_id(rule)

        if rule_id is None:
            await interaction.response.send_message(
                "Select a valid rule from the autocomplete list.",
                ephemeral=True,
            )
            return

        existing_rule = self.rules_store.get(
            interaction.guild.id,
            rule_id,
        )

        if existing_rule is None:
            await interaction.response.send_message(
                "That rule could not be found.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RuleModal(self, rule=existing_rule)
        )

    @app_commands.command(
        name="rule_remove",
        description="Remove a configured server rule.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    @app_commands.describe(rule="Rule to remove")
    @app_commands.autocomplete(rule=rule_autocomplete)
    async def rule_remove(
        self,
        interaction: discord.Interaction,
        rule: str,
    ) -> None:
        if not await self._require_enabled(interaction):
            return

        rule_id = self._parse_rule_id(rule)

        if rule_id is None:
            await interaction.response.send_message(
                "Select a valid rule from the autocomplete list.",
                ephemeral=True,
            )
            return

        existing_rule = self.rules_store.get(
            interaction.guild.id,
            rule_id,
        )

        if existing_rule is None:
            await interaction.response.send_message(
                "That rule could not be found.",
                ephemeral=True,
            )
            return

        deleted = self.rules_store.delete(
            interaction.guild.id,
            rule_id,
        )

        if not deleted:
            await interaction.response.send_message(
                "That rule could not be removed.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Removed rule **{existing_rule.title}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="rule_list",
        description="List this server's configured rules.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def rule_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._require_enabled(interaction):
            return

        rules = self.rules_store.list(
            interaction.guild.id,
            limit=25,
        )

        if not rules:
            await interaction.response.send_message(
                "No rules are configured yet. Use `/rule_add` to create one.",
                ephemeral=True,
            )
            return

        lines = [
            f"**{rule.title}** — `{rule.send_format}`"
            for rule in rules
        ]

        embed = discord.Embed(
            title="Configured Rules",
            description="\n".join(lines),
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text="Use /rule_edit or /rule_remove to manage rules."
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    # ============================================================ Send rules

    @app_commands.command(
        name="rules",
        description="Send a configured rule.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(moderate_members=True)
    @app_commands.describe(
        rule="Rule to send",
        format="Optional override: embed or text",
    )
    @app_commands.autocomplete(rule=rule_autocomplete)
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Embed", value="embed"),
            app_commands.Choice(name="Normal text", value="text"),
        ]
    )
    async def rules_menu(
        self,
        interaction: discord.Interaction,
        rule: str,
        format: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        if not await self._require_enabled(interaction):
            return

        rule_id = self._parse_rule_id(rule)

        if rule_id is None:
            await interaction.response.send_message(
                "Select a valid rule from the autocomplete list.",
                ephemeral=True,
            )
            return

        selected_rule = self.rules_store.get(
            interaction.guild.id,
            rule_id,
        )

        if selected_rule is None:
            await interaction.response.send_message(
                "That rule could not be found.",
                ephemeral=True,
            )
            return

        send_format = (
            format.value
            if format is not None
            else selected_rule.send_format
        )

        if send_format == "embed":
            embed = discord.Embed(
                title=selected_rule.title,
                description=selected_rule.content,
                color=EMBED_COLOR,
            )
            embed.set_footer(text="Rules are to be followed.")

            await interaction.response.send_message(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.response.send_message(
            f"**{selected_rule.title}**\n{selected_rule.content}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ============================================================ Errors

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = (
                "You need the required server permission to use that command."
            )
        else:
            print(f"[rules] {type(error).__name__}: {error}")
            message = "An unexpected rules error occurred."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RulesCommand(bot))