from __future__ import annotations

from error_handling import ErrorHandledModal, ErrorHandledView, ObserverCog

import json
import logging
import os
import re
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.config import is_bot_owner
from cogs.mod_stats import ModerationStatsStore
from cogs.setup_ui import (
    DB_PATH,
    MAX_PUNISHMENT_ROLES,
    SetupConfigStore,
)
from database import (
    BanAppealStore,
    ModerationActionStore,
    ModerationCaseStore,
    PunishmentRoleStore,
)
from moderation_notifications import dm_moderation_action

DEFAULT_LOG_COLOR = 0x96EDF1
MODULE_KEY = "moderation"
LEGACY_CONFIG_PATH = "mod_config.json"
logger = logging.getLogger("observer.moderation")
DURATION_PATTERN = re.compile(r"(\d+)([smhdw])", re.IGNORECASE)


def parse_ban_duration(value: str) -> timedelta:
    """Parse a compact temporary-ban duration such as 2d12h."""
    value = value.strip().lower()
    matches = list(DURATION_PATTERN.finditer(value))
    if not matches or "".join(match.group(0) for match in matches) != value:
        raise ValueError("Enter a duration such as 30m, 12h, or 7d; leave time blank for a permanent ban.")
    seconds_by_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    seconds = sum(
        int(match.group(1)) * seconds_by_unit[match.group(2)]
        for match in matches
    )
    if seconds <= 0 or seconds > 10 * 365 * 24 * 60 * 60:
        raise ValueError("Ban duration must be greater than zero and no longer than 10 years.")
    return timedelta(seconds=seconds)


def _load_legacy_config() -> dict:
    if not os.path.exists(LEGACY_CONFIG_PATH):
        return {}

    try:
        with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


async def mod_perms_check(interaction: discord.Interaction) -> bool:
    """Allow configured moderators, head moderators, or the bot owner."""
    if interaction.guild is None:
        return False

    if is_bot_owner(interaction.user):
        return True

    cog = interaction.client.get_cog("ModerationCog")

    if not isinstance(cog, ModerationCog):
        return False

    return await cog._check_mod_perms(interaction)


class ModerationUndoView(ErrorHandledView):
    """Persistent view for moderation log messages."""

    def __init__(self, cog: ModerationCog):
        super().__init__(timeout=None)
        self.cog = cog
        button = discord.ui.Button(
            label="Undo",
            style=discord.ButtonStyle.danger,
            custom_id="moderation_undo",
        )
        button.callback = self.undo
        self.add_item(button)

    async def undo(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message(
                "This action is only available in a server moderation log.",
                ephemeral=True,
            )
            return

        action = self.cog.actions.get_by_log_message(interaction.message.id)

        if action is None or action["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                "This moderation action could not be found.",
                ephemeral=True,
            )
            return

        if not await self.cog._head_mod_check(interaction):
            await interaction.response.send_message(
                "Only the head of moderation can undo actions.",
                ephemeral=True,
            )
            return

        if action["status"] != "active":
            await interaction.response.send_message(
                "This moderation action is no longer active and cannot be undone.",
                ephemeral=True,
            )
            return

        if not self.cog.actions.claim(action["id"]):
            await interaction.response.send_message(
                "This moderation action is no longer active and cannot be undone.",
                ephemeral=True,
            )
            return

        try:
            target = discord.Object(id=action["target_id"])

            if action["action_type"] == "ban":
                ban_entry = await interaction.guild.fetch_ban(target)
                await interaction.guild.unban(
                    target,
                    reason=f"Ban undone by {interaction.user}",
                )
                await dm_moderation_action(
                    ban_entry.user,
                    interaction.guild,
                    "Ban lifted",
                    f"Ban undone by {interaction.user}",
                )
                message = "Ban undone."
            elif action["action_type"] == "timeout":
                member = interaction.guild.get_member(action["target_id"])
                if member is None:
                    raise RuntimeError("Member is no longer in the server")
                await member.timeout(
                    None,
                    reason=f"Timeout undone by {interaction.user}",
                )
                await dm_moderation_action(
                    member,
                    interaction.guild,
                    "Timeout removed",
                    f"Timeout undone by {interaction.user}",
                )
                message = f"Removed timeout from {member.mention}."
            else:
                raise RuntimeError("Unsupported moderation action")
        except (discord.Forbidden, discord.HTTPException, RuntimeError):
            self.cog.actions.release(action["id"])
            await interaction.response.send_message(
                "Discord rejected the undo request.",
                ephemeral=True,
            )
            return

        self.cog.actions.complete(action["id"], interaction.user.id)
        case = self.cog.cases.get_case_by_action_id(
            interaction.guild.id,
            action["id"],
        )
        if case is not None:
            self.cog.cases.set_case_status(
                case["id"],
                "reverted",
                actor_id=interaction.user.id,
            )
        await interaction.response.send_message(message, ephemeral=True)
        try:
            if interaction.message.embeds:
                embed = discord.Embed.from_dict(
                    interaction.message.embeds[0].to_dict()
                )
                embed.title = f"{embed.title or 'Moderation action'} · Reverted"
                embed.add_field(
                    name="Status",
                    value=(
                        f"Reverted by {interaction.user.mention} "
                        f"(`{interaction.user}`)\n"
                        f"<t:{int(discord.utils.utcnow().timestamp())}:F>"
                    ),
                    inline=False,
                )
                await interaction.message.edit(
                    embed=embed,
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.message.edit(view=None)
        except discord.HTTPException:
            logger.exception(
                "Could not update moderation log after undoing action %s",
                action["id"],
            )


class PermanentBanDMView(ErrorHandledView):
    """Persistent appeal entry point included with permanent-ban notices."""

    def __init__(
        self,
        cog: ModerationCog,
        moderator_id: int | None = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        appeal = discord.ui.Button(
            label="Appeal in app",
            style=discord.ButtonStyle.primary,
            custom_id="moderation:ban_appeal",
        )
        appeal.callback = self.open_appeal
        self.add_item(appeal)
        if moderator_id is not None:
            self.add_item(
                discord.ui.Button(
                    label="Contact moderator",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/users/{moderator_id}",
                )
            )

    async def open_appeal(self, interaction: discord.Interaction) -> None:
        await self.cog.open_ban_appeal(interaction)


class BanAppealReviewView(discord.ui.View):
    """Persistent staff controls for an appeal submitted in a DM."""

    def __init__(self, cog: ModerationCog):
        super().__init__(timeout=None)
        self.cog = cog
        accept = discord.ui.Button(
            label="Accept and unban",
            style=discord.ButtonStyle.success,
            custom_id="moderation:appeal_accept",
        )
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id="moderation:appeal_reject",
        )
        accept.callback = self.accept
        reject.callback = self.reject
        self.add_item(accept)
        self.add_item(reject)

    async def accept(self, interaction: discord.Interaction) -> None:
        await self.cog.review_ban_appeal(interaction, "accepted")

    async def reject(self, interaction: discord.Interaction) -> None:
        await self.cog.review_ban_appeal(interaction, "rejected")


class BanAppealModal(ErrorHandledModal):
    def __init__(self, cog: ModerationCog, ban_case):
        super().__init__(title="Permanent Ban Appeal")
        self.cog = cog
        self.ban_case = ban_case
        self.appeal_reason = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Explain what happened and why the decision should be reviewed.",
            min_length=10,
            max_length=4000,
            required=True,
        )
        self.evidence = discord.ui.FileUpload(
            custom_id="appeal_evidence",
            required=False,
            min_values=0,
            max_values=3,
        )
        self.add_item(
            discord.ui.Label(
                text="Why should this ban be appealed?",
                description="The original ban reason will be included automatically for staff.",
                component=self.appeal_reason,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Image proof (optional)",
                description="Upload up to 3 screenshots or other images.",
                component=self.evidence,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submit_ban_appeal(
            interaction,
            self.ban_case,
            self.appeal_reason.value.strip(),
            list(self.evidence.values),
        )


class ModerationCog(ObserverCog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.mod_stats = ModerationStatsStore(DB_PATH)
        self.actions = ModerationActionStore(DB_PATH)
        self.cases = ModerationCaseStore(DB_PATH)
        self.appeals = BanAppealStore(DB_PATH)
        self.punishment_roles = PunishmentRoleStore(DB_PATH)
        self._legacy = _load_legacy_config()
        self.bot.add_view(ModerationUndoView(self))
        self.bot.add_view(PermanentBanDMView(self))
        self.bot.add_view(BanAppealReviewView(self))

    def _configured_punishment_role_ids(self, guild_id: int) -> set[int]:
        role_ids = set()
        for index in range(1, MAX_PUNISHMENT_ROLES + 1):
            value = self.store.get(
                guild_id,
                MODULE_KEY,
                f"punishment_role_{index}",
            )
            try:
                if value is not None:
                    role_ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return role_ids

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        configured = self._configured_punishment_role_ids(after.guild.id)
        remembered = self.punishment_roles.get_member_roles(
            after.guild.id,
            after.id,
        )
        tracked = configured | remembered
        after_roles = {role.id for role in after.roles}
        for role_id in tracked:
            if role_id in after_roles and role_id in configured:
                self.punishment_roles.remember(after.guild.id, after.id, role_id)
            elif role_id in remembered:
                # A moderator removing the punishment role ends its persistence.
                self.punishment_roles.forget(after.guild.id, after.id, role_id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        configured = self._configured_punishment_role_ids(member.guild.id)
        held = {role.id for role in member.roles} & configured
        # Snapshot the member's current punishments so removed or unconfigured
        # roles cannot linger as stale rejoin assignments.
        self.punishment_roles.set_member_roles(
            member.guild.id,
            member.id,
            held,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_id = member.guild.id
        configured = self._configured_punishment_role_ids(guild_id)
        remembered = self.punishment_roles.get_member_roles(guild_id, member.id)
        for role_id in remembered - configured:
            self.punishment_roles.forget(guild_id, member.id, role_id)

        bot_member = member.guild.me
        for role_id in sorted(remembered & configured):
            role = member.guild.get_role(role_id)
            if role is None:
                self.punishment_roles.forget(guild_id, member.id, role_id)
                continue
            if role in member.roles:
                continue
            if (
                bot_member is None
                or not bot_member.guild_permissions.manage_roles
                or role.managed
                or role >= bot_member.top_role
            ):
                logger.warning(
                    "Cannot restore punishment role %s for member %s in guild %s; "
                    "check Manage Roles permission and role hierarchy",
                    role_id,
                    member.id,
                    guild_id,
                )
                continue
            try:
                await member.add_roles(
                    role,
                    reason="Reapply configured punishment role after member rejoined",
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not restore punishment role %s for member %s in guild %s",
                    role_id,
                    member.id,
                    guild_id,
                    exc_info=True,
                )

    def get_appeals_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:
        try:
            channel_id = int(
                self.store.get(guild.id, MODULE_KEY, "appeal_channel")
            )
        except (TypeError, ValueError):
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def open_ban_appeal(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is not None or interaction.message is None:
            await interaction.response.send_message(
                "Open the appeal button from your permanent-ban DM.",
                ephemeral=True,
            )
            return
        ban_case = self.cases.get_case_by_appeal_message(interaction.message.id)
        if ban_case is None or interaction.user.id != ban_case["target_id"]:
            await interaction.response.send_message(
                "This appeal button is not linked to your ban.", ephemeral=True
            )
            return
        if (
            ban_case["case_type"] != "ban"
            or ban_case["status"] != "active"
            or ban_case["expires_at"] is not None
        ):
            await interaction.response.send_message(
                "This ban is no longer eligible for an appeal.", ephemeral=True
            )
            return
        guild = self.bot.get_guild(ban_case["guild_id"])
        if guild is None or self.get_appeals_channel(guild) is None:
            await interaction.response.send_message(
                "This server has not configured an appeals channel. Use the moderator contact option in your ban message.",
                ephemeral=True,
            )
            return
        existing = self.appeals.get_for_ban_case(guild.id, ban_case["id"])
        if existing is not None:
            label = {
                "pending": "Your appeal is already awaiting review.",
                "reviewing": "Your appeal is currently being reviewed.",
                "accepted": "Your appeal has already been accepted.",
                "rejected": "Your appeal has already been reviewed.",
            }.get(existing["status"], "An appeal has already been submitted for this ban.")
            await interaction.response.send_message(label, ephemeral=True)
            return
        await interaction.response.send_modal(BanAppealModal(self, ban_case))

    async def submit_ban_appeal(
        self,
        interaction: discord.Interaction,
        ban_case,
        reason: str,
        attachments: list[discord.Attachment],
    ) -> None:
        guild = self.bot.get_guild(ban_case["guild_id"])
        channel = self.get_appeals_channel(guild) if guild else None
        if guild is None or channel is None:
            await interaction.response.send_message(
                "This server's appeals channel is unavailable. Contact the moderator listed in your ban DM.",
                ephemeral=True,
            )
            return
        existing = self.appeals.get_for_ban_case(guild.id, ban_case["id"])
        if existing is not None:
            await interaction.response.send_message(
                "An appeal has already been submitted for this ban.",
                ephemeral=True,
            )
            return
        if not reason:
            await interaction.response.send_message(
                "Please explain why the ban should be appealed.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        files: list[discord.File] = []
        filenames: list[str] = []
        allowed_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        for attachment in attachments[:3]:
            filename = attachment.filename or "evidence"
            extension = os.path.splitext(filename)[1].lower()
            if not (
                (attachment.content_type or "").startswith("image/")
                or extension in allowed_extensions
            ):
                await interaction.followup.send(
                    f"`{filename}` is not an image. Please submit image proof only.",
                    ephemeral=True,
                )
                return
            try:
                files.append(await attachment.to_file())
                filenames.append(filename)
            except discord.HTTPException:
                await interaction.followup.send(
                    "I couldn't retrieve one of the uploaded images. Please submit the appeal again.",
                    ephemeral=True,
                )
                return

        appeal_case_id = self.cases.create_case(
            guild.id,
            "ban_appeal",
            ban_case["target_id"],
            reason=reason,
            reporter_id=interaction.user.id,
            status="open",
        )
        appeal_id, created = self.appeals.create(
            guild.id,
            ban_case["id"],
            appeal_case_id,
            ban_case["target_id"],
            reason,
            json.dumps(filenames),
            channel.id,
        )
        if not created:
            self.cases.set_case_status(appeal_case_id, "failed")
            await interaction.followup.send(
                "An appeal has already been submitted for this ban.",
                ephemeral=True,
            )
            return

        appeal_case = self.cases.get_case(appeal_case_id)
        embed = discord.Embed(
            title=f"Permanent Ban Appeal #{appeal_id} · Pending",
            description=(
                f"**Applicant:** <@{ban_case['target_id']}> (`{ban_case['target_id']}`)\n"
                f"**Banned by:** <@{ban_case['moderator_id']}>\n"
                f"**Ban case:** #{ban_case['case_number']} · ID `{ban_case['id']}`\n"
                f"**Appeal case:** #{appeal_case['case_number']} · ID `{appeal_case_id}`"
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Original ban reason",
            value=(ban_case["reason"] or "No reason provided")[:1000],
            inline=False,
        )
        embed.add_field(name="Why they are appealing", value=reason[:1000], inline=False)
        if files and files[0].filename:
            embed.set_image(url=f"attachment://{files[0].filename}")
        embed.set_footer(text="Only staff with moderation access should review this appeal.")
        try:
            review_message = await channel.send(
                embed=embed,
                files=files,
                view=BanAppealReviewView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            # No staff message means there is nothing to review. Remove the
            # unique pending row so the user can submit again after setup is fixed.
            self.appeals.delete(appeal_id)
            self.cases.set_case_status(appeal_case_id, "failed")
            await interaction.followup.send(
                "I couldn't deliver the appeal to staff. Contact the moderator listed in your ban DM.",
                ephemeral=True,
            )
            return
        self.appeals.set_staff_message(appeal_id, review_message.id)
        await interaction.followup.send(
            f"Your appeal was submitted to {guild.name}'s staff. Appeal #{appeal_id}.",
            ephemeral=True,
        )

    async def review_ban_appeal(
        self,
        interaction: discord.Interaction,
        decision: str,
    ) -> None:
        guild = interaction.guild
        if guild is None or interaction.message is None:
            await interaction.response.send_message(
                "Appeals can only be reviewed in the configured server channel.",
                ephemeral=True,
            )
            return
        member = interaction.user
        allowed = guild.owner_id == member.id or any(
            getattr(member.guild_permissions, permission, False)
            for permission in ("administrator", "ban_members", "moderate_members")
        )
        if not allowed and isinstance(member, discord.Member):
            allowed = await self._check_mod_perms(interaction)
        if not allowed:
            await interaction.response.send_message(
                "Only server moderators can review ban appeals.", ephemeral=True
            )
            return
        appeal = self.appeals.get_by_staff_message(interaction.message.id)
        if appeal is None or appeal["guild_id"] != guild.id:
            await interaction.response.send_message(
                "This appeal could not be found.", ephemeral=True
            )
            return
        if not self.appeals.claim_review(appeal["id"]):
            await interaction.response.send_message(
                "This appeal has already been reviewed.", ephemeral=True
            )
            return

        ban_case = self.cases.get_case(appeal["ban_case_id"])
        target = self.bot.get_user(appeal["target_id"])
        if target is None:
            try:
                target = await self.bot.fetch_user(appeal["target_id"])
            except discord.HTTPException:
                target = None

        if decision == "accepted":
            try:
                ban_entry = await guild.fetch_ban(
                    discord.Object(id=appeal["target_id"])
                )
                await guild.unban(
                    ban_entry.user,
                    reason=f"Ban appeal #{appeal['id']} accepted by {member}",
                )
                target = ban_entry.user
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                self.appeals.set_status(appeal["id"], "pending")
                await interaction.response.send_message(
                    "The appeal is still pending because I couldn't unban the user.",
                    ephemeral=True,
                )
                return
            if ban_case is not None:
                self.cases.set_case_status(
                    ban_case["id"], "reverted", actor_id=member.id
                )
                if ban_case["action_id"] is not None:
                    self.actions.mark_undone(ban_case["action_id"], member.id)
            appeal_case_status = "accepted"
            color = discord.Color.green()
            outcome = (
                f"Your permanent ban from {guild.name} was appealed successfully. "
                "The ban has been removed."
            )
            if target is not None:
                await dm_moderation_action(
                    target,
                    guild,
                    "Ban lifted",
                    f"Ban appeal #{appeal['id']} accepted by {member}",
                )
            unban_action_id = self.actions.create(
                guild.id,
                "unban",
                appeal["target_id"],
                member.id,
                status="resolved",
            )
            unban_case_id = self.cases.create_case(
                guild.id,
                "unban",
                appeal["target_id"],
                member.id,
                f"Ban appeal #{appeal['id']} accepted.",
                status="resolved",
                action_id=unban_action_id,
            )
            if isinstance(member, discord.Member):
                await self._send_mod_log(
                    guild=guild,
                    action="Appeal accepted; unbanned",
                    moderator=member,
                    target=target or discord.Object(id=appeal["target_id"]),
                    reason=f"Ban appeal #{appeal['id']} accepted.",
                    action_id=unban_action_id,
                    case_id=unban_case_id,
                    undoable=False,
                )
        else:
            appeal_case_status = "rejected"
            color = discord.Color.red()
            outcome = (
                f"Your appeal for the ban from {guild.name} was declined. "
                "The ban remains active."
            )
            if target is not None:
                try:
                    await target.send(
                        outcome,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

        self.appeals.set_status(
            appeal["id"], decision, reviewer_id=member.id
        )
        self.cases.set_case_status(appeal["appeal_case_id"], appeal_case_status)
        await interaction.response.send_message(
            f"Appeal #{appeal['id']} {decision}.", ephemeral=True
        )
        try:
            embed = (
                discord.Embed.from_dict(interaction.message.embeds[0].to_dict())
                if interaction.message.embeds
                else discord.Embed(title=f"Ban Appeal #{appeal['id']}")
            )
            embed.title = f"Permanent Ban Appeal #{appeal['id']} · {decision.title()}"
            embed.color = color
            embed.add_field(
                name="Decision",
                value=f"Reviewed by {member.mention} (`{member}`) · <t:{int(discord.utils.utcnow().timestamp())}:F>",
                inline=False,
            )
            await interaction.message.edit(
                embed=embed,
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("Could not update staff message for appeal %s", appeal["id"])

    async def cog_load(self) -> None:
        self.expire_moderation_cases.start()

    def cog_unload(self) -> None:
        self.expire_moderation_cases.cancel()

    @tasks.loop(minutes=1)
    async def expire_moderation_cases(self) -> None:
        now = discord.utils.utcnow().isoformat()
        for case in self.cases.list_due_cases(now):
            guild = self.bot.get_guild(case["guild_id"])
            if guild is None:
                continue

            if case["case_type"] == "timeout":
                self.cases.set_case_status(case["id"], "expired")
                if case["action_id"] is not None:
                    self.actions.set_status(case["action_id"], "expired")
                continue

            target = discord.Object(id=case["target_id"])
            user = None
            try:
                ban_entry = await guild.fetch_ban(target)
                user = ban_entry.user
                await guild.unban(
                    target,
                    reason=f"Temporary ban expired (Case #{case['case_number']})",
                )
            except discord.NotFound:
                user = self.bot.get_user(case["target_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Could not expire temporary ban case %s in guild %s",
                    case["id"],
                    guild.id,
                )
                continue

            self.cases.set_case_status(case["id"], "expired")
            if case["action_id"] is not None:
                self.actions.set_status(case["action_id"], "expired")
            if user is None:
                try:
                    user = await self.bot.fetch_user(case["target_id"])
                except discord.HTTPException:
                    user = None
            if user is not None:
                await dm_moderation_action(
                    user,
                    guild,
                    "Temporary ban expired; you have been unbanned",
                    f"The ban duration elapsed. Original reason: {case['reason'] or 'No reason provided'}",
                )
                if guild.me is not None:
                    await self._send_mod_log(
                        guild=guild,
                        action="Temporary ban expired",
                        moderator=guild.me,
                        target=user,
                        reason=case["reason"],
                        action_id=case["action_id"],
                        case_id=case["id"],
                        undoable=False,
                    )

    @expire_moderation_cases.before_loop
    async def before_expire_moderation_cases(self) -> None:
        await self.bot.wait_until_ready()

    # ============================================================ Confiiiiig

    def _legacy_guild_config(self, guild_id: int) -> dict:
        return self._legacy.get(str(guild_id), {})

    async def _get_guild_config(self, guild: discord.Guild) -> dict:
        guild_id = guild.id
        legacy = self._legacy_guild_config(guild_id)

        def pick(key: str, legacy_key: str):
            value = self.store.get(
                guild_id,
                MODULE_KEY,
                key,
            )

            if value is None:
                value = legacy.get(legacy_key)

            return value

        color = pick("embed_color", "embed_color")

        if color is None:
            color = DEFAULT_LOG_COLOR
        elif isinstance(color, str):
            try:
                color = int(color.strip().lstrip("#"), 16)
            except ValueError:
                color = DEFAULT_LOG_COLOR

        return {
            "log_channel": pick("log_channel", "log_channel"),
            "mod_role": pick("mod_role", "mod_role"),
            "head_mod_role": pick(
                "head_mod_role",
                "head_mod_role",
            ),
            "ping_mod_role": bool(
                self.store.get(
                    guild_id,
                    MODULE_KEY,
                    "ping_mod_role",
                    default=False,
                )
            ),
            "embed_color": int(color),
        }

    async def _is_logging_enabled(self, guild: discord.Guild) -> bool:
        return bool(
            self.store.get(
                guild.id,
                MODULE_KEY,
                "enabled",
                default=True,
            )
        )

    async def _get_moderator_role_ping(
        self,
        guild: discord.Guild,
    ) -> tuple[str, discord.AllowedMentions]:
        """
        Return the configured moderator-role mention when enabled.

        The role is read from moderation.mod_role and the toggle is read
        from moderation.ping_mod_role.
        """
        config = await self._get_guild_config(guild)

        if not config.get("ping_mod_role", False):
            return "", discord.AllowedMentions.none()

        role_id = config.get("mod_role")

        try:
            role = guild.get_role(int(role_id))
        except (TypeError, ValueError):
            return "", discord.AllowedMentions.none()

        if role is None or role.is_default():
            return "", discord.AllowedMentions.none()

        return (
            role.mention,
            discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )

    async def _check_mod_perms(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            return False

        if not isinstance(interaction.user, discord.Member):
            return False

        config = await self._get_guild_config(interaction.guild)

        def has_role(role_id: object) -> bool:
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                return False

            return any(
                role.id == role_id
                for role in interaction.user.roles
            )

        return (
            has_role(config.get("mod_role"))
            or has_role(config.get("head_mod_role"))
        )

    async def _head_mod_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None:
            return False

        if is_bot_owner(interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            return False

        config = await self._get_guild_config(interaction.guild)

        try:
            head_mod_role_id = int(
                config.get("head_mod_role")
            )
        except (TypeError, ValueError):
            return False

        return any(
            role.id == head_mod_role_id
            for role in interaction.user.roles
        )

    async def _get_log_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:
        if not await self._is_logging_enabled(guild):
            return None

        config = await self._get_guild_config(guild)

        try:
            log_channel_id = int(config.get("log_channel"))
        except (TypeError, ValueError):
            return None

        channel = guild.get_channel(log_channel_id)

        if not isinstance(channel, discord.TextChannel):
            return None

        return channel

    async def _send_mod_log(
        self,
        guild: discord.Guild,
        action: str,
        moderator: discord.Member,
        target: discord.abc.User,
        reason: str | None,
        action_id: int | None = None,
        action_ids: list[int] | None = None,
        case_id: int | None = None,
        case_ids: list[int] | None = None,
        undoable: bool = True,
        undo_callback=None,
    ) -> None:
        """
        Send a normal moderation log.

        Normal moderation logs never ping the moderator role. Only report
        messages use the optional moderator-role notification.
        """
        channel = await self._get_log_channel(guild)

        if channel is None:
            return

        config = await self._get_guild_config(guild)

        embed = discord.Embed(
            title=f"{action} executed",
            color=config.get(
                "embed_color",
                DEFAULT_LOG_COLOR,
            ),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name="Moderator",
            value=(
                f"{moderator.mention} "
                f"(`{moderator}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Target",
            value=(
                f"{target.mention} "
                f"(`{target}`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="Reason",
            value=(reason or "No reason provided")[:1000],
            inline=False,
        )

        if case_id is not None:
            case = self.cases.get_case(case_id)
            if case is not None:
                embed.add_field(
                    name="Case",
                    value=f"#{case['case_number']} · ID `{case_id}`",
                    inline=True,
                )
        elif case_ids:
            summaries = []
            for linked_case_id in case_ids[:10]:
                case = self.cases.get_case(linked_case_id)
                if case is not None:
                    summaries.append(
                        f"#{case['case_number']} (ID `{linked_case_id}`)"
                    )
            if len(case_ids) > 10:
                summaries.append(f"and {len(case_ids) - 10} more")
            if summaries:
                embed.add_field(
                    name="Cases",
                    value=", ".join(summaries),
                    inline=False,
                )
        if action_id is not None:
            embed.add_field(name="Action ID", value=f"`{action_id}`", inline=True)
        elif action_ids:
            embed.add_field(
                name="Action IDs",
                value=", ".join(f"`{value}`" for value in action_ids[:20]),
                inline=False,
            )
            if len(action_ids) > 20:
                embed.add_field(
                    name="More action IDs",
                    value=f"{len(action_ids) - 20} additional IDs are available in the individual cases.",
                    inline=False,
                )

        view = ModerationUndoView(self) if action_id is not None and undoable else None

        try:
            message = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if action_id is not None:
                self.actions.set_log_message(action_id, message.id)
            elif action_ids:
                for linked_action_id in action_ids:
                    self.actions.set_log_message(linked_action_id, message.id)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def send_report_log(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
        channel: discord.TextChannel | None = None,
    ) -> bool:
        """
        Send a report embed and optionally ping the moderator role.

        If `channel` is provided, the report is sent there. Otherwise the
        configured moderation log channel is used.

        Returns True when Discord accepts the message.
        """
        if not await self._is_logging_enabled(guild):
            return False

        if channel is None:
            channel = await self._get_log_channel(guild)

        if not isinstance(channel, discord.TextChannel):
            return False

        content, allowed_mentions = (
            await self._get_moderator_role_ping(guild)
        )

        try:
            await channel.send(
                content=content or None,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ============================================================ Location checker

    async def _validate_target(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        action: str,
    ) -> bool:
        if interaction.guild is None:
            return False

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return False

        if member == interaction.user:
            await interaction.response.send_message(
                f"You cannot {action} yourself.",
                ephemeral=True,
            )
            return False

        if member.id == interaction.guild.owner_id:
            await interaction.response.send_message(
                f"You cannot {action} the server owner.", ephemeral=True
            )
            return False

        if (
            interaction.user.id != interaction.guild.owner_id
            and member.top_role >= interaction.user.top_role
        ):
            await interaction.response.send_message(
                (
                    f"You cannot {action} someone with equal or "
                    "higher roles."
                ),
                ephemeral=True,
            )
            return False

        bot_member = interaction.guild.me

        if (
            bot_member is not None
            and member.top_role >= bot_member.top_role
        ):
            await interaction.response.send_message(
                (
                    "I cannot act on that member because their top "
                    "role is equal to or higher than my top role."
                ),
                ephemeral=True,
            )
            return False

        return True

    # ============================================================ The purge!!!!

    @app_commands.command(
        name="purge",
        description="Delete recent messages from this channel.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        amount="Number of messages to delete, from 1 to 1000.",
        reason="Reason for deleting the messages.",
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return

        if not isinstance(
            interaction.channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                (
                    "This command can only be used in a normal "
                    "text channel."
                ),
                ephemeral=True,
            )
            return

        if amount < 1 or amount > 1000:
            await interaction.response.send_message(
                "The amount must be between 1 and 1000.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me

        if bot_member is None:
            await interaction.response.send_message(
                "I could not find my member information in this server.",
                ephemeral=True,
            )
            return

        permissions = interaction.channel.permissions_for(
            bot_member
        )

        required = {
            "View Channel": permissions.view_channel,
            "Read Message History": (
                permissions.read_message_history
            ),
            "Manage Messages": permissions.manage_messages,
            "Send Messages": permissions.send_messages,
        }

        missing = [
            name
            for name, allowed in required.items()
            if not allowed
        ]

        if missing:
            await interaction.response.send_message(
                (
                    "I am missing these permissions in this channel: "
                    + ", ".join(missing)
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted_messages = await interaction.channel.purge(
                limit=amount,
                reason=reason or f"Purge by {interaction.user}",
            )

            affected_users: dict[int, tuple[discord.abc.User, int]] = {}
            for deleted_message in deleted_messages:
                author = deleted_message.author
                if author.bot:
                    continue
                previous = affected_users.get(author.id)
                affected_users[author.id] = (author, (previous[1] if previous else 0) + 1)
            case_ids = []
            action_ids = []
            for author, message_count in affected_users.values():
                case_reason = (
                    f"{message_count} message(s) deleted from "
                    f"#{interaction.channel.name}."
                    + (f" Reason: {reason}" if reason else "")
                )
                action_id = self.actions.create(
                    interaction.guild.id,
                    "purge",
                    author.id,
                    interaction.user.id,
                    status="resolved",
                )
                action_ids.append(action_id)
                case_ids.append(self.cases.create_case(
                    interaction.guild.id,
                    "purge",
                    author.id,
                    interaction.user.id,
                    case_reason,
                    status="resolved",
                    action_id=action_id,
                ))
            async def undo_purge(
                undo_interaction: discord.Interaction,
            ) -> None:
                await undo_interaction.response.send_message(
                    "A purge cannot be undone.",
                    ephemeral=True,
                )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Purge",
                moderator=interaction.user,
                target=interaction.user,
                reason=reason,
                action_ids=action_ids,
                case_ids=case_ids,
                undo_callback=undo_purge,
            )

            await interaction.followup.send(
                f"Deleted {len(deleted_messages)} message(s).",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                (
                    "I do not have permission to delete messages "
                    "in this channel."
                ),
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the purge request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Ban

    @app_commands.command(
        name="ban",
        description="Ban a member; leave time blank for a permanent ban or enter a duration for a temporary ban.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to ban.",
        time="Optional temporary-ban duration like 30m, 12h, or 7d. Leave blank for a permanent ban.",
        reason="Reason for the ban.",
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        time: str | None = None,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not await self._validate_target(
            interaction,
            member,
            "ban",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        try:
            ban_duration = parse_ban_duration(time) if time else None
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        ban_expires_at = (
            discord.utils.utcnow() + ban_duration
            if ban_duration is not None
            else None
        )
        expires_at = ban_expires_at.isoformat() if ban_expires_at else None

        await interaction.response.defer(ephemeral=True)

        try:
            action_id = self.actions.create(
                interaction.guild.id,
                "ban",
                member.id,
                interaction.user.id,
                status="pending",
            )
            case_id = self.cases.create_case(
                interaction.guild.id,
                "ban",
                member.id,
                interaction.user.id,
                reason,
                status="pending",
                action_id=action_id,
                expires_at=expires_at,
            )
            ban_dm_view = None
            if ban_expires_at is None and self.get_appeals_channel(interaction.guild):
                ban_dm_view = PermanentBanDMView(
                    self,
                    moderator_id=interaction.user.id,
                )
            # DM before applying the ban so the member receives the notice
            # while they are still in the guild. A closed DM inbox should not
            # prevent moderation from proceeding.
            dm_message = await dm_moderation_action(
                member,
                interaction.guild,
                "Ban",
                reason,
                duration=time,
                moderator=interaction.user,
                view=ban_dm_view,
            )
            if dm_message is not None and ban_dm_view is not None:
                self.cases.set_appeal_message(case_id, dm_message.id)
            try:
                await member.ban(reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                self.cases.set_case_status(case_id, "failed")
                self.actions.set_status(action_id, "failed")
                if dm_message is not None:
                    try:
                        await dm_message.edit(
                            content=(
                                f"The ban from **{interaction.guild.name}** "
                                "could not be applied. Please contact the moderator "
                                "listed above if you need clarification."
                            ),
                            view=None,
                        )
                    except discord.HTTPException:
                        pass
                raise
            self.cases.set_case_status(case_id, "active")
            self.actions.set_status(action_id, "active")
            action_label = (
                f"Temporary ban until <t:{int(ban_expires_at.timestamp())}:F>"
                if ban_expires_at is not None
                else "Permanent ban"
            )
            action_name = "Temporary Ban" if ban_expires_at else "Permanent Ban"

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "bans",
            )

            async def undo_ban(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                try:
                    await interaction.guild.unban(
                        member,
                        reason=(
                            f"Ban undone by "
                            f"{undo_interaction.user}"
                        ),
                    )

                    await undo_interaction.response.send_message(
                        f"Unbanned `{member}`.",
                        ephemeral=True,
                    )

                except discord.HTTPException:
                    await undo_interaction.response.send_message(
                        "Failed to unban the user.",
                        ephemeral=True,
                    )

            await self._send_mod_log(
                guild=interaction.guild,
                action=action_name,
                moderator=interaction.user,
                target=member,
                reason=reason,
                action_id=action_id,
                case_id=case_id,
            )

            await interaction.followup.send(
                f"{action_label} applied to `{member}`. Case #{self.cases.get_case(case_id)['case_number']}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to ban that member.",
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the ban request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Kick aka "Boot"

    @app_commands.command(
        name="unban",
        description="Remove a user's ban from the server.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        user_id="ID of the banned user (copy their ID from a mention or case log).",
        reason="Reason for removing the ban.",
    )
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return

        id_match = re.fullmatch(r"<@!?(\d+)>|\s*(\d+)\s*", user_id)
        if id_match is None:
            await interaction.response.send_message(
                "Enter the user's numeric Discord ID or paste their mention.",
                ephemeral=True,
            )
            return
        target_id = int(id_match.group(1) or id_match.group(2))

        await interaction.response.defer(ephemeral=True)
        active_ban_case = None
        try:
            ban_entry = await interaction.guild.fetch_ban(
                discord.Object(id=target_id)
            )
            user = ban_entry.user
            active_ban_case = self.cases.find_active_ban(
                interaction.guild.id,
                user.id,
            )
            await interaction.guild.unban(user, reason=reason)
        except discord.NotFound:
            await interaction.followup.send(
                f"`{user}` is not banned from this server.",
                ephemeral=True,
            )
            return

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to unban that user.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the unban request: `{error}`",
                ephemeral=True,
            )
            return

        if active_ban_case is not None:
            self.cases.set_case_status(
                active_ban_case["id"],
                "reverted",
                actor_id=interaction.user.id,
            )
            if active_ban_case["action_id"] is not None:
                self.actions.mark_undone(
                    active_ban_case["action_id"],
                    interaction.user.id,
                )

        action_id = self.actions.create(
            interaction.guild.id,
            "unban",
            user.id,
            interaction.user.id,
            status="resolved",
        )
        case_id = self.cases.create_case(
            interaction.guild.id,
            "unban",
            user.id,
            interaction.user.id,
            reason,
            status="resolved",
            action_id=action_id,
        )
        await dm_moderation_action(
            user,
            interaction.guild,
            "Ban lifted",
            reason,
        )
        await self._send_mod_log(
            guild=interaction.guild,
            action="Unban",
            moderator=interaction.user,
            target=user,
            reason=reason,
            action_id=action_id,
            case_id=case_id,
            undoable=False,
        )
        await interaction.followup.send(
            f"Unbanned `{user}`.",
            ephemeral=True,
        )

    # ============================================================ Warn

    @app_commands.command(
        name="warn",
        description="Warn a member and record it in their moderation history.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to warn.",
        reason="Reason for the warning.",
    )
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not await self._validate_target(interaction, member, "warn"):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)
        action_id = self.actions.create(
            interaction.guild.id,
            "warn",
            member.id,
            interaction.user.id,
            status="resolved",
        )
        case_id = self.cases.create_case(
            interaction.guild.id,
            "warn",
            member.id,
            interaction.user.id,
            reason,
            status="resolved",
            action_id=action_id,
        )
        await dm_moderation_action(
            member,
            interaction.guild,
            "Warning",
            reason,
        )
        await self._send_mod_log(
            guild=interaction.guild,
            action="Warning",
            moderator=interaction.user,
            target=member,
            reason=reason,
            action_id=action_id,
            case_id=case_id,
            undoable=False,
        )
        await interaction.followup.send(
            f"Warned `{member}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="kick",
        description="Kick a member from the server.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to kick.",
        reason="Reason for the kick.",
    )
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not await self._validate_target(
            interaction,
            member,
            "kick",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await member.kick(reason=reason)
            action_id = self.actions.create(
                interaction.guild.id,
                "kick",
                member.id,
                interaction.user.id,
                status="resolved",
            )
            case_id = self.cases.create_case(
                interaction.guild.id,
                "kick",
                member.id,
                interaction.user.id,
                reason,
                status="resolved",
                action_id=action_id,
            )
            await dm_moderation_action(
                member,
                interaction.guild,
                "Kick",
                reason,
            )

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "kicks",
            )

            async def undo_kick(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                await undo_interaction.response.send_message(
                    (
                        "A kick cannot be undone automatically. "
                        "The user must rejoin the server."
                    ),
                    ephemeral=True,
                )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Kick",
                moderator=interaction.user,
                target=member,
                reason=reason,
                action_id=action_id,
                case_id=case_id,
                undoable=False,
                undo_callback=undo_kick,
            )

            await interaction.followup.send(
                f"Kicked `{member}`.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to kick that member.",
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the kick request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Timeout stuff

    @app_commands.command(
        name="timeout",
        description="Timeout a member.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        member="Member to timeout.",
        duration_minutes=(
            "Timeout duration in minutes, from 1 to 40320."
        ),
        reason="Reason for the timeout.",
    )
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration_minutes: int,
        reason: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if duration_minutes < 1 or duration_minutes > 40320:
            await interaction.response.send_message(
                (
                    "The timeout must be between 1 and 40320 "
                    "minutes."
                ),
                ephemeral=True,
            )
            return

        if not await self._validate_target(
            interaction,
            member,
            "timeout",
        ):
            return

        if not isinstance(interaction.user, discord.Member):
            return

        await interaction.response.defer(ephemeral=True)

        try:
            timeout_until = (
                discord.utils.utcnow()
                + timedelta(minutes=duration_minutes)
            )

            await member.timeout(
                timeout_until,
                reason=reason,
            )
            await dm_moderation_action(
                member,
                interaction.guild,
                f"Timeout for {duration_minutes} minute(s)",
                reason,
            )

            action_id = self.actions.create(
                interaction.guild.id,
                "timeout",
                member.id,
                interaction.user.id,
            )
            case_id = self.cases.create_case(
                interaction.guild.id,
                "timeout",
                member.id,
                interaction.user.id,
                reason,
                status="active",
                action_id=action_id,
                expires_at=timeout_until.isoformat(),
            )

            self.mod_stats.increment(
                interaction.guild.id,
                interaction.user.id,
                "timeouts",
            )

            async def undo_timeout(
                undo_interaction: discord.Interaction,
            ) -> None:
                if not await self._head_mod_check(
                    undo_interaction
                ):
                    await undo_interaction.response.send_message(
                        (
                            "Only the head of moderation can "
                            "undo actions."
                        ),
                        ephemeral=True,
                    )
                    return

                try:
                    await member.timeout(
                        None,
                        reason=(
                            f"Timeout undone by "
                            f"{undo_interaction.user}"
                        ),
                    )

                    await undo_interaction.response.send_message(
                        f"Removed timeout from {member.mention}.",
                        ephemeral=True,
                    )

                except discord.HTTPException:
                    await undo_interaction.response.send_message(
                        "Failed to remove the timeout.",
                        ephemeral=True,
                    )

            await self._send_mod_log(
                guild=interaction.guild,
                action="Timeout",
                moderator=interaction.user,
                target=member,
                reason=reason,
                action_id=action_id,
                case_id=case_id,
            )

            await interaction.followup.send(
                (
                    f"Timed out {member.mention} for "
                    f"{duration_minutes} minute(s)."
                ),
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                (
                    "I do not have permission to timeout "
                    "that member."
                ),
                ephemeral=True,
            )

        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord rejected the timeout request: `{error}`",
                ephemeral=True,
            )

    # ============================================================ Mod chud stats

    @app_commands.command(
        name="modstats",
        description="View moderation statistics.",
    )
    @app_commands.guild_only()
    @app_commands.check(mod_perms_check)
    @app_commands.describe(
        moderator="Leave blank to view your own statistics.",
    )
    async def modstats(
        self,
        interaction: discord.Interaction,
        moderator: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Could not verify your server permissions.",
                ephemeral=True,
            )
            return

        target = moderator or interaction.user
        stats = self.mod_stats.get(
            interaction.guild.id,
            target.id,
        )

        total_actions = sum(stats.values())

        embed = discord.Embed(
            title=(
                f"Moderation Stats — "
                f"{target.display_name}"
            ),
            color=DEFAULT_LOG_COLOR,
        )

        embed.set_thumbnail(
            url=target.display_avatar.url
        )

        embed.add_field(
            name="Reports claimed",
            value=str(stats["reports_claimed"]),
            inline=True,
        )

        embed.add_field(
            name="Kicks",
            value=str(stats["kicks"]),
            inline=True,
        )

        embed.add_field(
            name="Bans",
            value=str(stats["bans"]),
            inline=True,
        )

        embed.add_field(
            name="Timeouts",
            value=str(stats["timeouts"]),
            inline=True,
        )

        embed.add_field(
            name="Appeals accepted",
            value=str(stats.get("appeals_accepted", 0)),
            inline=True,
        )

        embed.add_field(
            name="Appeals rejected",
            value=str(stats.get("appeals_rejected", 0)),
            inline=True,
        )

        embed.add_field(
            name="Total actions",
            value=str(total_actions),
            inline=True,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


    # ============================================================ Error handling



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
