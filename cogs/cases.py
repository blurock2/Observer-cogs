"""Private, server-scoped moderator case lookup and history."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import ModerationCaseStore, connect_sqlite
from error_handling import ErrorHandledView, ObserverCog


class CaseHistoryView(ErrorHandledView):
    def __init__(self, cog, guild_id, viewer_id, target_id, page=0):
        super().__init__(timeout=300)
        self.cog, self.guild_id, self.viewer_id, self.target_id = (
            cog,
            guild_id,
            viewer_id,
            target_id,
        )
        self.total, self.page, self.rows = cog.history_page(guild_id, target_id, page)
        self.pages = max(1, (self.total + 4) // 5)
        entries = self.rows
        if entries:
            select = discord.ui.Select(
                placeholder="Open a case",
                options=[
                    discord.SelectOption(
                        label=f"Case #{row['case_number']} · {row['case_type']}"[:100],
                        value=str(row["id"]),
                        description=f"ID {row['id']} · {row['created_at']}"[:100],
                    )
                    for row in entries
                ],
            )

            async def selected(interaction):
                row = self.cog.lookup(self.guild_id, case_id=int(select.values[0]))
                if row is None:
                    await interaction.response.send_message(
                        "Case not found in this server.", ephemeral=True
                    )
                    return
                await interaction.response.send_message(
                    embed=self.cog.case_embed(row),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            select.callback = selected
            self.add_item(select)
        for label, offset, disabled in [
            ("Previous", -1, self.page == 0),
            ("Next", 1, self.page >= self.pages - 1),
        ]:
            button = discord.ui.Button(label=label, disabled=disabled)

            async def navigate(interaction, offset=offset):
                view = CaseHistoryView(
                    self.cog,
                    self.guild_id,
                    self.viewer_id,
                    self.target_id,
                    self.page + offset,
                )
                await interaction.response.edit_message(embed=view.embed(), view=view)

            button.callback = navigate
            self.add_item(button)

    async def interaction_check(self, interaction):
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.viewer_id
            or not await self.cog.can_access(interaction)
        ):
            await interaction.response.send_message(
                "These private case controls require the original moderator and current staff access.",
                ephemeral=True,
            )
            return False
        return True

    def embed(self):
        embed = discord.Embed(
            title="Moderation history",
            description=f"Target: <@{self.target_id}> (`{self.target_id}`)",
            color=0x96EDF1,
        )
        for row in self.rows:
            details = [
                f"{self.cog.recorded_time(row)} · {self.cog.status_label(row)}",
                f"**Moderator:** <@{row['moderator_id']}>" if row["moderator_id"] else "**Moderator:** Not recorded",
                f"**Reason:** {(row['reason'] or 'No reason provided')[:350]}",
            ]
            if row["expires_at"]:
                details.append(f"**Until:** <t:{self.cog.timestamp(row['expires_at'])}:F>")
            if row["action_id"] is not None:
                details.append(f"**Action ID:** `{row['action_id']}`")
            embed.add_field(
                name=f"Case #{row['case_number']} · ID {row['id']} · {row['case_type']}"[
                    :256
                ],
                value="\n".join(details)[:1024],
                inline=False,
            )
        if not self.rows:
            embed.description += "\nNo cases are recorded for this user."
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.pages} · {self.total} cases · Select a case for details"
        )
        return embed


class Cases(ObserverCog):
    def __init__(self, bot):
        self.bot, self.db_path = bot, DB_PATH
        self.store = SetupConfigStore(self.db_path)
        self.cases = ModerationCaseStore(self.db_path)
        self.cases.import_legacy_actions()
        self.cases.link_unlinked_cases()

    async def can_access(self, interaction):
        if interaction.guild is None:
            return False
        if interaction.user.id == interaction.guild.owner_id:
            return True
        permissions = interaction.user.guild_permissions
        if any(
            getattr(permissions, key, False)
            for key in (
                "administrator",
                "moderate_members",
                "ban_members",
                "kick_members",
            )
        ):
            return True
        roles = {role.id for role in getattr(interaction.user, "roles", ())}
        moderation = self.bot.get_cog("ModerationCog")
        if moderation is not None and await moderation._check_mod_perms(interaction):
            return True
        for key in ("mod_role", "head_mod_role"):
            try:
                if (
                    int(self.store.get(interaction.guild.id, "moderation", key))
                    in roles
                ):
                    return True
            except (ValueError, TypeError):
                pass
        return False

    def lookup(self, guild_id, *, case_id=None, number=None):
        column, value = (
            ("id", case_id) if case_id is not None else ("case_number", number)
        )
        with connect_sqlite(self.db_path) as db:
            return db.execute(
                f"SELECT * FROM moderation_cases WHERE guild_id=? AND {column}=?",
                (guild_id, value),
            ).fetchone()

    def history(self, guild_id, target_id):
        return self.cases.list_user_history(guild_id, target_id)

    def history_page(self, guild_id, target_id, page=0):
        with connect_sqlite(self.db_path) as db:
            total = db.execute(
                "SELECT COUNT(*) FROM moderation_cases WHERE guild_id=? AND target_id=?",
                (guild_id, target_id),
            ).fetchone()[0]
            pages = max(1, (total + 4) // 5)
            page = max(0, min(page, pages - 1))
            rows = db.execute(
                "SELECT * FROM moderation_cases WHERE guild_id=? AND target_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 5 OFFSET ?",
                (guild_id, target_id, page * 5),
            ).fetchall()
        return total, page, rows

    def resolve_name(self, guild, value):
        lowered = value.casefold().lstrip("@")
        ids = {
            member.id
            for member in guild.members
            if any(
                str(name).casefold() == lowered
                for name in (
                    member.name,
                    member.display_name,
                    getattr(member, "global_name", None),
                )
                if name
            )
        }
        with connect_sqlite(self.db_path) as db:
            rows = db.execute(
                "SELECT DISTINCT user_id,name FROM user_name_history WHERE guild_id=?",
                (guild.id,),
            ).fetchall()
        ids.update(row["user_id"] for row in rows if row["name"].casefold() == lowered)
        return ids

    @staticmethod
    def recorded_time(row):
        try:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return f"<t:{int(created.timestamp())}:f>"
        except (TypeError, ValueError, OverflowError, OSError):
            return str(row["created_at"] or "Not recorded")[:100]

    @staticmethod
    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except (TypeError, ValueError, OverflowError, OSError):
            return 0

    @staticmethod
    def status_label(row):
        status = row["status"]
        if row["action_id"] is not None:
            with connect_sqlite(DB_PATH) as db:
                action = db.execute(
                    "SELECT status FROM moderation_actions WHERE id=? AND guild_id=?",
                    (row["action_id"], row["guild_id"]),
                ).fetchone()
            if action:
                if action["status"] == "undone":
                    return "Reverted"
                if action["status"] == "expired":
                    return "Expired"
                if action["status"] == "active":
                    return "Active"
        return {
            "active": "Active",
            "resolved": "Completed",
            "reverted": "Reverted",
            "undone": "Reverted",
            "expired": "Expired",
            "open": "Open",
        }.get(status, str(status).title())

    def case_embed(self, row):
        embed = discord.Embed(
            title=f"Case #{row['case_number']} · ID {row['id']}",
            description=f"**{str(row['case_type'])[:200]}** · {self.status_label(row)}",
            color=0x96EDF1,
        )
        embed.add_field(
            name="Member",
            value=f"<@{row['target_id']}>\n`{row['target_id']}`",
            inline=True,
        )
        embed.add_field(
            name="Moderator",
            value=f"<@{row['moderator_id']}>\n`{row['moderator_id']}`"
            if row["moderator_id"]
            else "Not recorded",
            inline=True,
        )
        embed.add_field(
            name="Reason",
            value=(row["reason"] or "No reason provided")[:1000],
            inline=False,
        )
        embed.add_field(name="Recorded", value=self.recorded_time(row), inline=False)
        if row["action_id"] is not None:
            embed.add_field(name="Action ID", value=str(row["action_id"]), inline=True)
        if row["expires_at"]:
            expiry = self.timestamp(row["expires_at"])
            embed.add_field(name="Expires", value=f"<t:{expiry}:F> (<t:{expiry}:R>)", inline=False)
        if row["source_message_id"]:
            embed.add_field(
                name="Source message ID",
                value=str(row["source_message_id"]),
                inline=False,
            )
        embed.set_footer(
            text="Recorded history; resolved means the case was handled, not that the punishment expired."
        )
        return embed

    @app_commands.command(
        name="case",
        description="Staff: look up a case or a member’s moderation history privately.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="Pick a member by username.",
        user_id="Discord user ID; works for members who left.",
        case_id="The global case ID displayed on a case card.",
        action_id="Undo/action ID printed in the moderation log.",
        query="Username, user ID, case:ID, action:ID, or #server-case-number.",
    )
    async def case(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        user_id: app_commands.Range[str, 1, 20] | None = None,
        case_id: app_commands.Range[int, 1] | None = None,
        action_id: app_commands.Range[int, 1] | None = None,
        query: app_commands.Range[str, 1, 100] | None = None,
    ):
        if not await self.can_access(interaction):
            await interaction.response.send_message(
                "Only moderators or configured staff can view cases.", ephemeral=True
            )
            return
        if sum(value is not None for value in (user, user_id, case_id, action_id, query)) != 1:
            await interaction.response.send_message(
                "Choose one lookup: user, user_id, case_id, action_id, or query.", ephemeral=True
            )
            return
        target_id = user.id if user else None
        number = None
        if query:
            value = query.strip()
            if re.fullmatch(r"action:\d+", value, re.IGNORECASE):
                action_id = int(value.split(":")[1])
            elif re.fullmatch(r"(?:case:|#)\d+", value, re.IGNORECASE):
                if value.startswith("#"):
                    number = int(value[1:])
                else:
                    case_id = int(value.split(":")[1])
            elif re.fullmatch(r"<@!?\d+>", value):
                target_id = int(re.sub(r"\D", "", value))
            elif value.isdecimal():
                if len(value) >= 17:
                    target_id = int(value)
                else:
                    case_id = int(value)
            else:
                ids = self.resolve_name(interaction.guild, value)
                if len(ids) != 1:
                    await interaction.response.send_message(
                        "No unique username match. Pick the member with user or supply their user_id.",
                        ephemeral=True,
                    )
                    return
                target_id = ids.pop()
        if user_id is not None:
            if not user_id.isdecimal() or not 0 < int(user_id) < 2**63:
                await interaction.response.send_message(
                    "Enter a valid numeric Discord user ID.", ephemeral=True
                )
                return
            target_id = int(user_id)
        if (
            (target_id is not None and not 0 < target_id < 2**63)
            or (case_id is not None and not 0 < case_id < 2**63)
            or (action_id is not None and not 0 < action_id < 2**63)
            or (number is not None and not 0 < number < 2**63)
        ):
            await interaction.response.send_message(
                "That ID is outside the valid range.", ephemeral=True
            )
            return
        if action_id is not None:
            row = self.cases.get_case_by_action_id(interaction.guild.id, action_id)
            if row is None:
                await interaction.response.send_message(
                    "No case is linked to that action ID in this server.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                embed=self.case_embed(row), ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        elif case_id is not None or number is not None:
            row = self.lookup(interaction.guild.id, case_id=case_id, number=number)
            if row is None:
                await interaction.response.send_message(
                    "Case not found in this server.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                embed=self.case_embed(row),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            view = CaseHistoryView(
                self, interaction.guild.id, interaction.user.id, target_id
            )
            await interaction.response.send_message(
                embed=view.embed(),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Cases(bot))
