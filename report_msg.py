  # ============================================================ No comments a bit lazy atm ill do it later
from __future__ import annotations

import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.mod_stats import ModerationStatsStore
from cogs.setup_ui import DB_PATH, SetupConfigStore


MODULE_KEY = "report_msg"
MODERATION_MODULE_KEY = "moderation"

NO_MENTIONS = discord.AllowedMentions.none()


class ClaimReportView(discord.ui.View):
    """
    Report-claim controls.

    Claim state is held in memory. Moderation-stat totals are persisted,
    but an individual report's claimed/unclaimed state is not restored
    after a bot restart.
    """

    def __init__(
        self,
        cog: "ReportMessage",
        staff_role_id: Optional[int],
        *,
        claimed_by: Optional[int] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.staff_role_id = staff_role_id
        self.claimed_by = claimed_by

    def is_staff(self, member: discord.Member) -> bool:
        if member.guild_permissions.manage_messages:
            return True

        return (
            self.staff_role_id is not None
            and any(
                role.id == self.staff_role_id
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

        if (
            interaction.message is None
            or not interaction.message.embeds
        ):
            await interaction.response.send_message(
                "The report embed could not be found.",
                ephemeral=True,
            )
            return

        self.claimed_by = member.id

        self.cog.mod_stats.increment(
            interaction.guild.id,
            member.id,
            "reports_claimed",
        )

        button.disabled = True
        button.label = f"Claimed by {member.display_name}"[:80]

        unclaim_button = discord.utils.get(
            self.children,
            custom_id="report_unclaim",
        )

        if isinstance(unclaim_button, discord.ui.Button):
            unclaim_button.disabled = False

        embed = interaction.message.embeds[0]

        embed.add_field(
            name="Claimed by",
            value=f"{member.mention} (`{member.id}`)",
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

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

        if self.claimed_by is None:
            await interaction.response.send_message(
                "This report has not been claimed.",
                ephemeral=True,
            )
            return

        member = interaction.user

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

        if (
            interaction.message is None
            or not interaction.message.embeds
        ):
            await interaction.response.send_message(
                "The report embed could not be found.",
                ephemeral=True,
            )
            return

        self.claimed_by = None

        claim_button = discord.utils.get(
            self.children,
            custom_id="report_claim",
        )

        if isinstance(claim_button, discord.ui.Button):
            claim_button.disabled = False
            claim_button.label = "Claim Report"

        button.disabled = True

        embed = interaction.message.embeds[0]

        for index, field in enumerate(embed.fields):
            if field.name == "Claimed by":
                embed.remove_field(index)
                break

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


class ReportModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "ReportMessage",
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
            await report_channel.send(
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

        self.cog.start_cooldown(
            guild.id,
            interaction.user.id,
        )

        await interaction.response.send_message(
            "Your report has been submitted. Thank you.",
            ephemeral=True,
        )


class ReportMessage(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.mod_stats = ModerationStatsStore(DB_PATH)
        self.cooldowns: dict[tuple[int, int], float] = {}

        self.report_command = app_commands.ContextMenu(
            name="Report message",
            callback=self.report_message,
        )

        self.bot.tree.add_command(
            self.report_command
        )

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
    ) -> Optional[int]:
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
    ) -> Optional[int]:
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
        return bool(
            self.get_moderation_config(
                guild_id,
                "ping_mod_role",
                False,
            )
        )

    def get_moderator_role_id(
        self,
        guild_id: int,
    ) -> Optional[int]:
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
        The optional notification uses moderation.mod_role and
        moderation.ping_mod_role.
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