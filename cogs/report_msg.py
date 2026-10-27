  # ============================================================ No comments a bit lazy atm ill do it later
from __future__ import annotations

from error_handling import ErrorHandledModal, ErrorHandledView, ObserverCog

import time
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.mod_stats import ModerationStatsStore
from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import connect_sqlite

MODULE_KEY = "report_msg"
MODERATION_MODULE_KEY = "moderation"

NO_MENTIONS = discord.AllowedMentions.none()
logger = logging.getLogger("observer.report_msg")


def _report_claimed_by(embed: discord.Embed) -> int | None:
    for field in embed.fields:
        if field.name.casefold() != "claimed by":
            continue
        match = re.search(
            r"<@!?(\d{15,22})>|\((\d{15,22})\)|\b(\d{15,22})\b",
            field.value,
        )
        if match:
            return int(next(group for group in match.groups() if group))
    return None


def _report_is_accepted(embed: discord.Embed) -> bool:
    return any(field.name.casefold() == "accepted by staff" for field in embed.fields)


class AcceptedReportStore:
    """Persist report authors and staff acceptance timestamps."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS accepted_report_tracking (
                    report_message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    reporter_id INTEGER NOT NULL,
                    accepted_at INTEGER
                )
                """
            )

    @contextmanager
    def _connect(self):
        db = connect_sqlite(self.db_path)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_report(self, message_id: int, guild_id: int, reporter_id: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO accepted_report_tracking "
                "(report_message_id, guild_id, reporter_id) VALUES (?, ?, ?)",
                (message_id, guild_id, reporter_id),
            )

    def accept(self, message_id: int, guild_id: int) -> tuple[int | None, bool]:
        with self._connect() as db:
            row = db.execute(
                "SELECT reporter_id, accepted_at FROM accepted_report_tracking "
                "WHERE report_message_id = ? AND guild_id = ?",
                (message_id, guild_id),
            ).fetchone()
            if row is None:
                return None, False
            if row["accepted_at"] is not None:
                return int(row["reporter_id"]), False
            db.execute(
                "UPDATE accepted_report_tracking SET accepted_at = ? "
                "WHERE report_message_id = ? AND accepted_at IS NULL",
                (int(datetime.now(timezone.utc).timestamp()), message_id),
            )
            return int(row["reporter_id"]), True

    def unaccept(self, message_id: int, guild_id: int) -> tuple[int | None, int | None]:
        """Clear an accepted report and return its reporter and acceptance time."""
        with self._connect() as db:
            row = db.execute(
                "SELECT reporter_id, accepted_at FROM accepted_report_tracking "
                "WHERE report_message_id = ? AND guild_id = ?",
                (message_id, guild_id),
            ).fetchone()
            if row is None or row["accepted_at"] is None:
                return (int(row["reporter_id"]) if row else None), None
            accepted_at = int(row["accepted_at"])
            db.execute(
                "UPDATE accepted_report_tracking SET accepted_at = NULL "
                "WHERE report_message_id = ? AND guild_id = ?",
                (message_id, guild_id),
            )
            return int(row["reporter_id"]), accepted_at

    def monthly_leader(self, guild_id: int, month: str) -> tuple[int | None, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT reporter_id, COUNT(*) AS total "
                "FROM accepted_report_tracking "
                "WHERE guild_id = ? AND accepted_at IS NOT NULL "
                "AND strftime('%Y-%m', accepted_at, 'unixepoch') = ? "
                "GROUP BY reporter_id ORDER BY total DESC, reporter_id ASC",
                (guild_id, month),
            ).fetchall()
        if not rows:
            return None, 0
        return int(rows[0]["reporter_id"]), int(rows[0]["total"])

    def monthly_counts(self, guild_id: int, month: str) -> list[tuple[int, int]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT reporter_id, COUNT(*) AS total "
                "FROM accepted_report_tracking "
                "WHERE guild_id = ? AND accepted_at IS NOT NULL "
                "AND strftime('%Y-%m', accepted_at, 'unixepoch') = ? "
                "GROUP BY reporter_id ORDER BY total DESC, reporter_id ASC",
                (guild_id, month),
            ).fetchall()
        return [(int(row["reporter_id"]), int(row["total"])) for row in rows]


class ClaimReportView(ErrorHandledView):
    """
    Report-claim controls.

    Claim state is mirrored in the report embed so it can be restored
    when persistent views are registered after a bot restart.
    """

    def __init__(
        self,
        cog: ReportMessage,
        staff_role_id: int | None,
        *,
        claimed_by: int | None = None,
        accepted: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.staff_role_id = staff_role_id
        self.claimed_by = claimed_by
        self.accepted = accepted
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "report_claim" and claimed_by is not None:
                child.disabled = True
                child.label = "Report Claimed"
            elif child.custom_id == "report_unclaim":
                child.disabled = claimed_by is None
            elif child.custom_id == "report_accept":
                child.disabled = accepted or claimed_by is None
                if accepted:
                    child.label = "Accepted"
                elif claimed_by is None:
                    child.label = "Claim report first"

    def is_staff(self, member: discord.Member) -> bool:
        if member.guild_permissions.manage_messages:
            return True

        staff_role_id = self.cog.get_staff_role_id(member.guild.id) or self.staff_role_id
        return (
            staff_role_id is not None
            and any(
                role.id == staff_role_id
                for role in member.roles
            )
        )

    @discord.ui.button(
        label="Claim Report",
        style=discord.ButtonStyle.primary,
        emoji="🗿",
        custom_id="report_claim",
    )
    async def claim_report(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Your server member information could not be found.",
                ephemeral=True,
            )
            return

        if (
            interaction.message is None
            or not interaction.message.embeds
        ):
            await interaction.response.send_message(
                "The report embed could not be found.",
                ephemeral=True,
            )
            return

        embed = interaction.message.embeds[0]
        self.claimed_by = _report_claimed_by(embed)
        self.accepted = _report_is_accepted(embed)
        if self.claimed_by is not None:
            await interaction.response.send_message(
                "This report has already been claimed.",
                ephemeral=True,
            )
            return

        member = interaction.user

        if not self.is_staff(member):
            await interaction.response.send_message(
                "Only staff members can claim reports.",
                ephemeral=True,
            )
            return

        self.cog.mod_stats.increment(
            interaction.guild.id,
            member.id,
            "reports_claimed",
        )

        embed.add_field(
            name="Claimed by",
            value=f"{member.mention} (`{member.id}`)",
            inline=False,
        )

        view = ClaimReportView(
            self.cog,
            self.cog.get_staff_role_id(interaction.guild.id),
            claimed_by=member.id,
            accepted=self.accepted,
        )
        claim_button = discord.utils.get(view.children, custom_id="report_claim")
        if isinstance(claim_button, discord.ui.Button):
            claim_button.label = f"Claimed by {member.display_name}"[:80]
        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )
        self.cog.bot.add_view(view, message_id=interaction.message.id)

    @discord.ui.button(
        label="Unclaim Report",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="report_unclaim",
        disabled=True,
    )
    async def unclaim_report(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This button can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Your server member information could not be found.",
                ephemeral=True,
            )
            return

        member = interaction.user

        if interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message(
                "The report embed could not be found.", ephemeral=True
            )
            return

        embed = interaction.message.embeds[0]
        self.claimed_by = _report_claimed_by(embed)
        self.accepted = _report_is_accepted(embed)
        if self.claimed_by is None:
            await interaction.response.send_message(
                "This report has not been claimed.", ephemeral=True
            )
            return

        if (
            member.id != self.claimed_by
            and not self.is_staff(member)
        ):
            await interaction.response.send_message(
                (
                    "Only the staff member who claimed this report, "
                    "or another staff member, can unclaim it."
                ),
                ephemeral=True,
            )
            return

        was_accepted = self.accepted
        was_accepted = was_accepted or _report_is_accepted(embed)
        self.cog.reports.unaccept(interaction.message.id, interaction.guild.id)

        for index in reversed(range(len(embed.fields))):
            if embed.fields[index].name.casefold() in {"claimed by", "accepted by staff"}:
                embed.remove_field(index)

        view = ClaimReportView(
            self.cog,
            self.cog.get_staff_role_id(interaction.guild.id),
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view,
        )
        self.cog.bot.add_view(view, message_id=interaction.message.id)
        if was_accepted:
            try:
                await self.cog.update_monthly_reporter_role(
                    interaction.guild, datetime.now(timezone.utc).strftime("%Y-%m")
                )
            except discord.HTTPException:
                logger.warning(
                    "Report %s was reopened, but its monthly role update failed",
                    interaction.message.id,
                    exc_info=True,
                )

    @discord.ui.button(
        label="Accept Report",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="report_accept",
    )
    async def accept_report(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message(
                "This action can only be used on a report in a server.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.user, discord.Member) or not self.is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can accept reports.", ephemeral=True
            )
            return
        if interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message(
                "The report embed could not be found.", ephemeral=True
            )
            return
        embed = interaction.message.embeds[0]
        self.claimed_by = _report_claimed_by(embed)
        self.accepted = _report_is_accepted(embed)
        if self.claimed_by != interaction.user.id:
            await interaction.response.send_message(
                "Claim this report before accepting it. Only its claimant can take report actions.",
                ephemeral=True,
            )
            return

        if self.accepted:
            await interaction.response.send_message(
                "This report has already been accepted.", ephemeral=True
            )
            return

        reporter_id, newly_accepted = self.cog.reports.accept(
            interaction.message.id, interaction.guild.id
        )
        if reporter_id is None:
            await interaction.response.send_message(
                "I couldn't find this report in my records. Reports from before this feature was enabled cannot be accepted for the monthly award.",
                ephemeral=True,
            )
            return
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        winner_id, total, role_message = await self.cog.update_monthly_reporter_role(
            interaction.guild, month
        )
        if newly_accepted:
            embed.add_field(
                name="Accepted by staff",
                value=f"{interaction.user.mention} • <t:{int(datetime.now(timezone.utc).timestamp())}:f>",
                inline=False,
            )
        view = ClaimReportView(
            self.cog,
            self.cog.get_staff_role_id(interaction.guild.id),
            claimed_by=self.claimed_by,
            accepted=True,
        )
        await interaction.response.edit_message(embed=embed, view=view)
        self.cog.bot.add_view(view, message_id=interaction.message.id)

        status = role_message if newly_accepted else f"This report was already accepted. {role_message}"
        if winner_id is not None:
            status += f" Monthly leader: <@{winner_id}> with {total} accepted report(s)."
        await interaction.followup.send(status, ephemeral=True, allowed_mentions=NO_MENTIONS)


class ReportModal(ErrorHandledModal):
    def __init__(
        self,
        cog: ReportMessage,
        reported_message: discord.Message,
    ):
        super().__init__(title="Report Message")

        self.cog = cog
        self.reported_message = reported_message

        self.reason = discord.ui.TextInput(
            label="Why are you reporting this message?",
            placeholder=(
                "Explain the reason for this report..."
            ),
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=1000,
            required=True,
        )

        self.add_item(self.reason)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                (
                    "This report can only be submitted "
                    "inside a server."
                ),
                ephemeral=True,
            )
            return

        if not self.cog.is_enabled(guild.id):
            await interaction.response.send_message(
                "Message reporting is disabled in this server.",
                ephemeral=True,
            )
            return

        report_channel_id = (
            self.cog.get_report_channel_id(guild.id)
        )

        if report_channel_id is None:
            await interaction.response.send_message(
                (
                    "Reports have not been configured yet. "
                    "Ask an administrator to configure a "
                    "reports channel in `/setup`."
                ),
                ephemeral=True,
            )
            return

        report_channel = guild.get_channel(
            report_channel_id
        )

        if not isinstance(
            report_channel,
            discord.TextChannel,
        ):
            try:
                fetched = await self.cog.bot.fetch_channel(
                    report_channel_id
                )
            except discord.DiscordException:
                fetched = None

            report_channel = (
                fetched
                if isinstance(fetched, discord.TextChannel)
                else None
            )

        if report_channel is None:
            await interaction.response.send_message(
                (
                    "The configured report channel no longer "
                    "exists or is not a text channel."
                ),
                ephemeral=True,
            )
            return

        message = self.reported_message
        message_content = message.content.strip()

        if not message_content:
            message_content = (
                "*No text content. The message may contain "
                "an attachment or embed.*"
            )

        if len(message_content) > 1024:
            message_content = message_content[:1021] + "..."

        embed = discord.Embed(
            title="New Message Report",
            colour=discord.Colour.red(),
            timestamp=discord.utils.utcnow(),
        )

        if not self.cog.is_anonymous(guild.id):
            embed.add_field(
                name="Reported by",
                value=(
                    f"{interaction.user.mention} "
                    f"(`{interaction.user.id}`)"
                ),
                inline=False,
            )

        embed.add_field(
            name="Message author",
            value=(
                f"{message.author.mention} "
                f"(`{message.author.id}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Channel",
            value=(
                f"{message.channel.mention} "
                f"(`{message.channel.id}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Reason",
            value=self.reason.value,
            inline=False,
        )

        embed.add_field(
            name="Message content",
            value=message_content,
            inline=False,
        )

        if message.attachments:
            attachment_urls = "\n".join(
                attachment.url
                for attachment in message.attachments
            )

            embed.add_field(
                name="Attachments",
                value=attachment_urls[:1024],
                inline=False,
            )
            image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
            image_attachment = next((
                attachment
                for attachment in message.attachments
                if (attachment.content_type or "").startswith("image/")
                or "." + (attachment.filename or "").rsplit(".", 1)[-1].lower() in image_extensions
            ), None)
            if image_attachment is not None:
                embed.set_image(url=image_attachment.url)

        embed.add_field(
            name="Message link",
            value=f"[Jump to message]({message.jump_url})",
            inline=False,
        )

        embed.set_thumbnail(
            url=message.author.display_avatar.url
        )

        staff_role_id = self.cog.get_staff_role_id(
            guild.id
        )

        ping_content, allowed_mentions = (
            self.cog.get_moderator_role_notification(
                guild
            )
        )

        try:
            report_message = await report_channel.send(
                content=ping_content or None,
                embed=embed,
                view=ClaimReportView(
                    self.cog,
                    staff_role_id,
                ),
                allowed_mentions=allowed_mentions,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                (
                    "I do not have permission to send reports "
                    "to the configured reports channel."
                ),
                ephemeral=True,
            )
            return

        except discord.HTTPException:
            await interaction.response.send_message(
                (
                    "Discord returned an error while sending "
                    "the report."
                ),
                ephemeral=True,
            )
            return

        self.cog.reports.add_report(
            report_message.id, guild.id, interaction.user.id
        )

        self.cog.start_cooldown(
            guild.id,
            interaction.user.id,
        )

        await interaction.response.send_message(
            "Your report has been submitted. Thank you.",
            ephemeral=True,
        )


class ReportMessage(ObserverCog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.mod_stats = ModerationStatsStore(DB_PATH)
        self.reports = AcceptedReportStore(DB_PATH)
        self.cooldowns: dict[tuple[int, int], float] = {}
        self._report_views_restored = False

        self.report_command = app_commands.ContextMenu(
            name="Report message",
            callback=self.report_message,
        )

        self.bot.tree.add_command(
            self.report_command
        )
        self.bot.add_view(ClaimReportView(self, None))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._report_views_restored:
            return

        for guild in self.bot.guilds:
            channel_id = self.get_report_channel_id(guild.id)
            channel = guild.get_channel(channel_id) if channel_id else None
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue

            try:
                async for message in channel.history(limit=None):
                    embed = next(
                        (item for item in message.embeds if item.title == "New Message Report"),
                        None,
                    )
                    if embed is None:
                        continue

                    # A previous integration or another bot may have posted
                    # report embeds in this channel. Discord only lets the
                    # author edit a message, so skip those instead of logging
                    # a Forbidden traceback on every startup.
                    if self.bot.user is None or message.author.id != self.bot.user.id:
                        continue

                    claimed_by = _report_claimed_by(embed)
                    accepted = _report_is_accepted(embed)

                    view = ClaimReportView(
                        self,
                        self.get_staff_role_id(guild.id),
                        claimed_by=claimed_by,
                        accepted=accepted,
                    )
                    current_controls = {
                        (component.custom_id, component.label, component.disabled)
                        for row in message.components
                        for component in row.children
                        if getattr(component, "custom_id", None) is not None
                    }
                    expected_controls = {
                        (component.custom_id, component.label, component.disabled)
                        for component in view.children
                        if getattr(component, "custom_id", None) is not None
                    }
                    try:
                        # Refresh stale/legacy component layouts as well as
                        # restoring callbacks, so old report buttons respond.
                        if current_controls != expected_controls:
                            await message.edit(view=view)
                        self.bot.add_view(view, message_id=message.id)
                    except discord.HTTPException:
                        logger.warning(
                            "Could not restore report controls for message %s in guild %s",
                            message.id,
                            guild.id,
                            exc_info=True,
                        )
            except discord.Forbidden:
                logger.warning(
                    "Cannot restore report controls in guild %s: missing history or message permissions",
                    guild.id,
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not restore report controls in guild %s",
                    guild.id,
                    exc_info=True,
                )
        logger.info("Restored persistent report controls for guilds with configured report channels")
        self._report_views_restored = True

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.report_command.name,
            type=self.report_command.type,
        )

    def get_config(
        self,
        guild_id: int,
        key: str,
        default=None,
    ):
        return self.store.get(
            guild_id,
            MODULE_KEY,
            key,
            default,
        )

    def get_moderation_config(
        self,
        guild_id: int,
        key: str,
        default=None,
    ):
        return self.store.get(
            guild_id,
            MODERATION_MODULE_KEY,
            key,
            default,
        )

    def is_enabled(self, guild_id: int) -> bool:
        return bool(
            self.get_config(
                guild_id,
                "enabled",
                False,
            )
        )

    def get_report_channel_id(
        self,
        guild_id: int,
    ) -> int | None:
        value = self.get_config(
            guild_id,
            "report_channel",
        )

        try:
            return (
                int(value)
                if value is not None
                else None
            )
        except (TypeError, ValueError):
            return None

    def get_staff_role_id(
        self,
        guild_id: int,
    ) -> int | None:
        value = self.get_config(
            guild_id,
            "staff_role",
        )

        try:
            return (
                int(value)
                if value is not None
                else None
            )
        except (TypeError, ValueError):
            return None

    def is_anonymous(self, guild_id: int) -> bool:
        return bool(
            self.get_config(
                guild_id,
                "anonymous",
                False,
            )
        )

    def get_cooldown(self, guild_id: int) -> int:
        value = self.get_config(
            guild_id,
            "cooldown",
            30,
        )

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 30

    def is_moderator_role_ping_enabled(
        self,
        guild_id: int,
    ) -> bool:
        setting = self.get_config(guild_id, "ping_mod_role")
        if setting is None:
            setting = self.get_moderation_config(
                guild_id,
                "ping_mod_role",
                False,
            )
        return bool(setting)

    def get_moderator_role_id(
        self,
        guild_id: int,
    ) -> int | None:
        value = self.get_moderation_config(
            guild_id,
            "mod_role",
        )

        try:
            return (
                int(value)
                if value is not None
                else None
            )
        except (TypeError, ValueError):
            return None

    def get_moderator_role_notification(
        self,
        guild: discord.Guild,
    ) -> tuple[str, discord.AllowedMentions]:
        """
        Return the optional moderator-role ping for a report.

        The report destination and claim role come from report_msg.
        The notification toggle is report-specific, while the notified
        role is shared with moderation.
        """
        if not self.is_moderator_role_ping_enabled(
            guild.id
        ):
            return "", NO_MENTIONS

        moderator_role_id = self.get_moderator_role_id(
            guild.id
        )

        if moderator_role_id is None:
            return "", NO_MENTIONS

        moderator_role = guild.get_role(
            moderator_role_id
        )

        if moderator_role is None:
            return "", NO_MENTIONS

        if moderator_role.is_default():
            return "", NO_MENTIONS

        return (
            moderator_role.mention,
            discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=True,
                replied_user=False,
            ),
        )

    def cooldown_remaining(
        self,
        guild_id: int,
        user_id: int,
    ) -> float:
        cooldown = self.get_cooldown(guild_id)

        if cooldown <= 0:
            return 0

        last_report_at = self.cooldowns.get(
            (guild_id, user_id)
        )

        if last_report_at is None:
            return 0

        return max(
            0,
            cooldown
            - (time.monotonic() - last_report_at),
        )

    def start_cooldown(
        self,
        guild_id: int,
        user_id: int,
    ) -> None:
        self.cooldowns[
            (guild_id, user_id)
        ] = time.monotonic()

    async def update_monthly_reporter_role(
        self, guild: discord.Guild, month: str
    ) -> tuple[int | None, int, str]:
        role_id = self.get_config(guild.id, "monthly_top_reporter_role")
        try:
            role_id = int(role_id) if role_id is not None else None
        except (TypeError, ValueError):
            role_id = None
        if role_id is None:
            return None, 0, "No monthly award role is configured."

        role = guild.get_role(role_id)
        if role is None:
            return None, 0, "The configured monthly award role no longer exists."

        counts = self.reports.monthly_counts(guild.id, month)
        if not counts:
            for holder in list(role.members):
                await holder.remove_roles(role, reason=f"No accepted-report leader remains ({month})")
            return None, 0, "No accepted reports remain this month; the monthly award role was cleared."

        top_count = counts[0][1]
        tied_ids = {user_id for user_id, count in counts if count == top_count}

        # Keep the existing holder on a tie. If the previous leader is no
        # longer tied, the lowest user ID wins so the result is deterministic.
        current_tied = sorted(member.id for member in role.members if member.id in tied_ids)
        winner_id = current_tied[0] if current_tied else min(tied_ids)

        for holder in list(role.members):
            if holder.id != winner_id:
                await holder.remove_roles(role, reason=f"Monthly accepted-report leader changed ({month})")

        winner = guild.get_member(winner_id)
        if winner is None:
            try:
                winner = await guild.fetch_member(winner_id)
            except discord.NotFound:
                return winner_id, top_count, "The report was accepted, but the monthly leader is no longer in this server."
        if role not in winner.roles:
            await winner.add_roles(role, reason=f"Monthly accepted-report leader ({month})")
        return winner_id, top_count, "Monthly award role updated."

    async def report_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if (
            message.guild is None
            or message.guild.id != guild.id
        ):
            await interaction.response.send_message(
                "That message does not belong to this server.",
                ephemeral=True,
            )
            return

        if not self.is_enabled(guild.id):
            await interaction.response.send_message(
                "Message reporting is disabled in this server.",
                ephemeral=True,
            )
            return

        if (
            self.get_report_channel_id(guild.id)
            is None
        ):
            await interaction.response.send_message(
                (
                    "Reports have not been configured yet. "
                    "Ask an administrator to configure a "
                    "reports channel in `/setup`."
                ),
                ephemeral=True,
            )
            return

        remaining = self.cooldown_remaining(
            guild.id,
            interaction.user.id,
        )

        if remaining > 0:
            await interaction.response.send_message(
                (
                    f"Please wait {remaining:.0f} seconds "
                    "before submitting another report."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            ReportModal(self, message)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReportMessage(bot))
