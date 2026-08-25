from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import (
    DB_PATH,
    SetupConfigStore,
    owner_or_has_permissions,
)


MODULE_KEY = "tickets"
NO_MENTIONS = discord.AllowedMentions.none()


class TicketPanelView(discord.ui.View):
    """Persistent Create Ticket button shown in panel channels."""

    def __init__(self, cog: "TicketCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.green,
        emoji="🎫",
        custom_id="tickets:create_ticket",
    )
    async def create_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.create_ticket(interaction)


class TicketCloseView(discord.ui.View):
    """Persistent Close Ticket button inside every ticket."""

    def __init__(self, cog: "TicketCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.red,
        emoji="🔒",
        custom_id="tickets:close_ticket",
    )
    async def close_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.close_ticket(interaction)


class TicketCog(commands.Cog):
    """
    Support ticket system.

    Dashboard settings:
    - tickets.enabled
    - tickets.panel_channel
    - tickets.support_role
    - tickets.ping_support_role
    - tickets.blacklisted_role
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

        self.panel_view: Optional[TicketPanelView] = None
        self.close_view: Optional[TicketCloseView] = None

    async def cog_load(self) -> None:
        """Register stable views so ticket buttons work after restarts."""
        self.panel_view = TicketPanelView(self)
        self.close_view = TicketCloseView(self)

        self.bot.add_view(self.panel_view)
        self.bot.add_view(self.close_view)

    def cog_unload(self) -> None:
        if self.panel_view is not None:
            self.bot.remove_view(self.panel_view)

        if self.close_view is not None:
            self.bot.remove_view(self.close_view)

    # ============================================================ Dsashboard config

    def _get(
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

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(
            self._get(
                guild_id,
                "enabled",
                default=True,
            )
        )

    def _get_id(
        self,
        guild_id: int,
        key: str,
    ) -> Optional[int]:
        value = self._get(guild_id, key)

        try:
            return (
                int(value)
                if value is not None
                else None
            )
        except (TypeError, ValueError):
            return None

    def _panel_channel_id(
        self,
        guild_id: int,
    ) -> Optional[int]:
        return self._get_id(
            guild_id,
            "panel_channel",
        )

    def _support_role_id(
        self,
        guild_id: int,
    ) -> Optional[int]:
        return self._get_id(
            guild_id,
            "support_role",
        )

    def _blacklisted_role_id(
        self,
        guild_id: int,
    ) -> Optional[int]:
        return self._get_id(
            guild_id,
            "blacklisted_role",
        )

    def _should_ping_support_role(
        self,
        guild_id: int,
    ) -> bool:
        return bool(
            self._get(
                guild_id,
                "ping_support_role",
                default=False,
            )
        )

    def _get_support_role_notification(
        self,
        guild: discord.Guild,
        support_role: discord.Role,
    ) -> tuple[str, discord.AllowedMentions]:
        """
        Return the optional support-role ping.

        The support role is only mentioned when the setup toggle is
        enabled. Otherwise, all mentions remain disabled.
        """
        if not self._should_ping_support_role(guild.id):
            return "", NO_MENTIONS

        if support_role.is_default():
            return "", NO_MENTIONS

        return (
            support_role.mention,
            discord.AllowedMentions(
                users=False,
                roles=True,
                everyone=False,
                replied_user=False,
            ),
        )

    # ============================================================ Ticket helpers 

    @staticmethod
    def _ticket_name(user: discord.Member) -> str:
        return f"ticket-{user.id}"

    def _get_existing_ticket(
        self,
        guild: discord.Guild,
        user: discord.Member,
    ) -> Optional[discord.TextChannel]:
        expected_name = self._ticket_name(user)

        for channel in guild.text_channels:
            if channel.name == expected_name:
                return channel

        return None

    @staticmethod
    def _is_ticket_channel(
        channel: discord.TextChannel,
    ) -> bool:
        return (
            channel.name.startswith("ticket-")
            and channel.topic is not None
            and "Ticket owner ID:" in channel.topic
        )

    @staticmethod
    def _ticket_owner_id(
        channel: discord.TextChannel,
    ) -> Optional[int]:
        if channel.topic is None:
            return None

        prefix = "Ticket owner ID:"

        if prefix not in channel.topic:
            return None

        try:
            return int(
                channel.topic.split(
                    prefix,
                    1,
                )[1].strip().split()[0]
            )
        except (ValueError, IndexError):
            return None

    # ============================================================ Pannel spawn

    @app_commands.command(
        name="ticket_panel",
        description="Post or repost the ticket creation panel.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def ticket_panel(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            return

        if not self._is_enabled(guild.id):
            await interaction.response.send_message(
                (
                    "The ticket system is disabled. Enable it "
                    "through `/setup` first."
                ),
                ephemeral=True,
            )
            return

        panel_channel_id = self._panel_channel_id(guild.id)
        support_role_id = self._support_role_id(guild.id)

        if panel_channel_id is None:
            await interaction.response.send_message(
                "Set a **Panel channel** in `/setup` first.",
                ephemeral=True,
            )
            return

        if support_role_id is None:
            await interaction.response.send_message(
                "Set a **Support role** in `/setup` first.",
                ephemeral=True,
            )
            return

        panel_channel = guild.get_channel(panel_channel_id)

        if not isinstance(
            panel_channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                (
                    "The configured panel channel is invalid "
                    "or is not a text channel."
                ),
                ephemeral=True,
            )
            return

        support_role = guild.get_role(support_role_id)

        if support_role is None:
            await interaction.response.send_message(
                "The configured support role no longer exists.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Support Tickets",
            description=(
                "Need help?\n"
                "Click the button below to open a private "
                "support ticket."
            ),
            color=discord.Color.yellow(),
        )

        embed.set_footer(
            text=(
                "Please do not open multiple tickets "
                "for the same issue."
            )
        )

        try:
            await panel_channel.send(
                embed=embed,
                view=TicketPanelView(self),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                (
                    f"I cannot send messages in "
                    f"{panel_channel.mention}."
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                (
                    "Discord rejected the ticket-panel "
                    "message."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                f"Ticket panel posted in "
                f"{panel_channel.mention}."
            ),
            ephemeral=True,
        )

    # ============================================================ Spawn a ticket

    async def create_ticket(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild
        user = interaction.user

        if (
            guild is None
            or not isinstance(user, discord.Member)
        ):
            await interaction.response.send_message(
                "Tickets can only be created inside a server.",
                ephemeral=True,
            )
            return

        if not self._is_enabled(guild.id):
            await interaction.response.send_message(
                "The ticket system is disabled in this server.",
                ephemeral=True,
            )
            return

        panel_channel_id = self._panel_channel_id(guild.id)
        support_role_id = self._support_role_id(guild.id)
        blacklisted_role_id = (
            self._blacklisted_role_id(guild.id)
        )

        if (
            panel_channel_id is None
            or support_role_id is None
        ):
            await interaction.response.send_message(
                (
                    "The ticket system has not been "
                    "configured correctly yet."
                ),
                ephemeral=True,
            )
            return

        if blacklisted_role_id is not None:
            blacklisted_role = guild.get_role(
                blacklisted_role_id
            )

            if (
                blacklisted_role is not None
                and blacklisted_role in user.roles
            ):
                await interaction.response.send_message(
                    (
                        "You are not allowed to create "
                        "support tickets."
                    ),
                    ephemeral=True,
                )
                return

        panel_channel = guild.get_channel(
            panel_channel_id
        )
        support_role = guild.get_role(
            support_role_id
        )

        if not isinstance(
            panel_channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                (
                    "The configured panel channel is invalid "
                    "or no longer a text channel."
                ),
                ephemeral=True,
            )
            return

        if support_role is None:
            await interaction.response.send_message(
                (
                    "The configured support role no longer "
                    "exists."
                ),
                ephemeral=True,
            )
            return

        existing_ticket = self._get_existing_ticket(
            guild,
            user,
        )

        if existing_ticket is not None:
            await interaction.response.send_message(
                (
                    f"You already have an open ticket: "
                    f"{existing_ticket.mention}"
                ),
                ephemeral=True,
            )
            return

        bot_member = guild.me

        if bot_member is None:
            await interaction.response.send_message(
                (
                    "I could not find my server member "
                    "information."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        overwrites: dict[
            discord.Role | discord.Member,
            discord.PermissionOverwrite,
        ] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            support_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        try:
            ticket_channel = await guild.create_text_channel(
                name=self._ticket_name(user),
                category=panel_channel.category,
                overwrites=overwrites,
                topic=f"Ticket owner ID: {user.id}",
                reason=(
                    f"Support ticket created by {user}"
                ),
            )

            ticket_embed = discord.Embed(
                title="Support Ticket",
                description=(
                    f"Welcome {user.mention}!\n\n"
                    "Please explain your issue in detail. "
                    "A support member will assist you shortly."
                ),
                color=discord.Color.yellow(),
            )

            ticket_embed.add_field(
                name="Ticket owner",
                value=user.mention,
                inline=True,
            )

            ticket_embed.set_footer(
                text=(
                    "Use the button below to close "
                    "this ticket."
                )
            )

            support_ping, support_mentions = (
                self._get_support_role_notification(
                    guild,
                    support_role,
                )
            )

            ticket_content = "\n".join(
                value
                for value in (
                    support_ping,
                    user.mention,
                )
                if value
            )

            allowed_mentions = discord.AllowedMentions(
                users=True,
                roles=support_mentions.roles,
                everyone=False,
                replied_user=False,
            )

            await ticket_channel.send(
                content=ticket_content or None,
                embed=ticket_embed,
                view=TicketCloseView(self),
                allowed_mentions=allowed_mentions,
            )

            await interaction.followup.send(
                (
                    f"Your ticket has been created: "
                    f"{ticket_channel.mention}"
                ),
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                (
                    "I cannot create ticket channels. Give me "
                    "**Manage Channels** and permission to manage "
                    "channel overwrites."
                ),
                ephemeral=True,
            )

        except discord.HTTPException as error:
            print(
                f"[tickets] Error while creating ticket: "
                f"{error}"
            )

            await interaction.followup.send(
                (
                    "Discord rejected the ticket creation "
                    "request."
                ),
                ephemeral=True,
            )

    # ============================================================ Rage quit the ticket

    async def close_ticket(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if (
            guild is None
            or not isinstance(channel, discord.TextChannel)
        ):
            await interaction.response.send_message(
                "This is not a valid ticket channel.",
                ephemeral=True,
            )
            return

        if not self._is_ticket_channel(channel):
            await interaction.response.send_message(
                (
                    "This button can only be used inside "
                    "a ticket channel."
                ),
                ephemeral=True,
            )
            return

        if not isinstance(user, discord.Member):
            await interaction.response.send_message(
                (
                    "Could not verify your server member "
                    "information."
                ),
                ephemeral=True,
            )
            return

        support_role_id = self._support_role_id(
            guild.id
        )

        support_role = (
            guild.get_role(support_role_id)
            if support_role_id is not None
            else None
        )

        is_support_staff = (
            support_role is not None
            and support_role in user.roles
        )

        is_server_admin = (
            user.guild_permissions.manage_channels
        )

        ticket_owner_id = self._ticket_owner_id(
            channel
        )

        is_ticket_owner = ticket_owner_id == user.id

        if not (
            is_support_staff
            or is_server_admin
            or is_ticket_owner
        ):
            await interaction.response.send_message(
                (
                    "Only the ticket owner, support staff, "
                    "or an administrator can close this ticket."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "This ticket will be deleted in 5 seconds.",
            ephemeral=True,
        )

        await asyncio.sleep(5)

        try:
            await channel.delete(
                reason=(
                    f"Ticket closed by {user} "
                    f"({user.id})"
                ),
            )

        except discord.NotFound:
            pass

        except discord.Forbidden:
            try:
                await interaction.followup.send(
                    (
                        "I do not have permission to delete "
                        "this ticket channel."
                    ),
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

        except discord.HTTPException as error:
            print(
                f"[tickets] Error while deleting ticket: "
                f"{error}"
            )

    # ============================================================ Errors

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(
            error,
            app_commands.MissingPermissions,
        ):
            message = (
                "You need **Manage Server** permission to "
                "post a ticket panel (unless you are the "
                "bot owner)."
            )
        else:
            print(
                f"[tickets] {type(error).__name__}: {error}"
            )
            message = (
                "An unexpected ticket-system error occurred."
            )

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))