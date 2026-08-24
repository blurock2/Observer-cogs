from __future__ import annotations

import asyncio
import re
from typing import Optional

import discord
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore


MODULE_KEY = "temporary_voice"

ROOMS_KEY = "rooms"
DEFAULT_ROOM_NAME = "{user}'s channel"


class TemporaryVoice(commands.Cog):
    """
    Join-to-create temporary voice rooms.

    Dashboard settings:
    - temporary_voice.enabled
    - temporary_voice.join_channel
    - temporary_voice.category
    - temporary_voice.rename_on_join
    - temporary_voice.default_name

    Internal stored setting:
    - temporary_voice.rooms
      {voice_channel_id: {"owner_id": member_id, "locked": bool}}
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self._creation_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """
        Restore persistent views for currently tracked temporary rooms.

        The room-control message itself remains in the channel; adding a
        view lets matching persistent button IDs work again after restart.
        """
        for guild in self.bot.guilds:
            rooms = self._get_rooms(guild.id)

            for channel_id in rooms:
                try:
                    voice_channel_id = int(channel_id)
                except (TypeError, ValueError):
                    continue

                self.bot.add_view(
                    VoiceControlView(
                        cog=self,
                        voice_channel_id=voice_channel_id,
                    )
                )

    # ============================================================ Setup config

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _set(self, guild_id: int, key: str, value) -> None:
        self.store.set(guild_id, MODULE_KEY, key, value)

    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "enabled", default=False))

    def _get_join_channel_id(self, guild_id: int) -> Optional[int]:
        value = self._get(guild_id, "join_channel")

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _get_category_id(self, guild_id: int) -> Optional[int]:
        value = self._get(guild_id, "category")

        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _rename_on_join(self, guild_id: int) -> bool:
        return bool(self._get(guild_id, "rename_on_join", default=True))

    def _default_name(self, guild_id: int) -> str:
        value = self._get(
            guild_id,
            "default_name",
            default=DEFAULT_ROOM_NAME,
        )

        value = str(value).strip() if value is not None else ""

        return value or DEFAULT_ROOM_NAME

    # ============================================================ Room storage

    def _get_rooms(self, guild_id: int) -> dict[str, dict]:
        rooms = self._get(guild_id, ROOMS_KEY, default={})

        return rooms if isinstance(rooms, dict) else {}

    def _save_rooms(self, guild_id: int, rooms: dict[str, dict]) -> None:
        self._set(guild_id, ROOMS_KEY, rooms)

    def _get_room(
        self,
        guild_id: int,
        voice_channel_id: int,
    ) -> Optional[dict]:
        return self._get_rooms(guild_id).get(str(voice_channel_id))

    def _set_room(
        self,
        guild_id: int,
        voice_channel_id: int,
        room: dict,
    ) -> None:
        rooms = self._get_rooms(guild_id)
        rooms[str(voice_channel_id)] = room
        self._save_rooms(guild_id, rooms)

    def _remove_room(
        self,
        guild_id: int,
        voice_channel_id: int,
    ) -> Optional[dict]:
        rooms = self._get_rooms(guild_id)
        room = rooms.pop(str(voice_channel_id), None)
        self._save_rooms(guild_id, rooms)
        return room

    def _is_temporary_room(
        self,
        guild_id: int,
        channel_id: int,
    ) -> bool:
        return self._get_room(guild_id, channel_id) is not None

    def _get_creation_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._creation_locks.get(guild_id)

        if lock is None:
            lock = asyncio.Lock()
            self._creation_locks[guild_id] = lock

        return lock

    # ============================================================ Room helpers

    @staticmethod
    def _build_room_name(
        member: discord.Member,
        template: str,
        rename_on_join: bool,
    ) -> str:
        if rename_on_join:
            name = template.replace("{user}", member.display_name)
        else:
            name = template.replace("{user}", "User")

        name = name.strip()

        return name[:100] or "Temporary Voice"

    async def _get_owned_room(
        self,
        interaction: discord.Interaction,
    ) -> Optional[discord.VoiceChannel]:
        if interaction.guild is None:
            return None

        if not isinstance(interaction.user, discord.Member):
            return None

        voice_state = interaction.user.voice

        if voice_state is None:
            return None

        channel = voice_state.channel

        if not isinstance(channel, discord.VoiceChannel):
            return None

        room = self._get_room(interaction.guild.id, channel.id)

        if room is None:
            return None

        if room.get("owner_id") != interaction.user.id:
            return None

        return channel

    async def _owner_only(
        self,
        interaction: discord.Interaction,
    ) -> Optional[discord.VoiceChannel]:
        channel = await self._get_owned_room(interaction)

        if channel is not None:
            return channel

        message = (
            "You must be inside your own temporary voice channel "
            "to use this button."
        )

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

        return None

    async def _create_room(
        self,
        member: discord.Member,
        category: discord.CategoryChannel,
    ) -> Optional[discord.VoiceChannel]:
        guild = member.guild
        room_name = self._build_room_name(
            member,
            self._default_name(guild.id),
            self._rename_on_join(guild.id),
        )

        overwrites: dict[
            discord.abc.Snowflake,
            discord.PermissionOverwrite,
        ] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                read_message_history=True,
                send_messages=False,
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                read_message_history=True,
                send_messages=True,
                use_application_commands=True,
                move_members=True,
                mute_members=True,
                deafen_members=True,
            ),
        }

        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                read_message_history=True,
                send_messages=True,
                manage_channels=True,
            )

        try:
            channel = await guild.create_voice_channel(
                name=room_name,
                category=category,
                overwrites=overwrites,
                reason="Creating temporary voice channel",
            )
        except discord.Forbidden:
            print(
                "[temporary_voice] Missing Manage Channels permission "
                f"in {guild.name} ({guild.id})."
            )
            return None
        except discord.HTTPException as error:
            print(
                "[temporary_voice] Failed to create room in "
                f"{guild.name} ({guild.id}): {error}"
            )
            return None

        self._set_room(
            guild.id,
            channel.id,
            {
                "owner_id": member.id,
                "locked": False,
            },
        )

        return channel

    async def _send_control_panel(
        self,
        channel: discord.VoiceChannel,
    ) -> None:
        view = VoiceControlView(
            cog=self,
            voice_channel_id=channel.id,
        )

        embed = discord.Embed(
            title="Voice Channel Controls",
            description=(
                "Use the buttons below to control your room.\n\n"
                "🔒 **Lock** prevents new users from joining.\n"
                "👤 **Invite** lets a selected member join a locked room."
            ),
            color=discord.Color.blurple(),
        )

        try:
            await channel.send(embed=embed, view=view)
        except AttributeError:
            print(
                "[temporary_voice] Your installed discord.py version "
                "does not support messages in voice-channel chat."
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            print(
                "[temporary_voice] Could not send room controls in "
                f"{channel.guild.name} ({channel.guild.id}): {error}"
            )

    async def _delete_room(
        self,
        voice_channel: discord.VoiceChannel,
    ) -> None:
        guild = voice_channel.guild
        room = self._remove_room(guild.id, voice_channel.id)

        if room is None:
            return

        try:
            await voice_channel.delete(
                reason="Temporary voice channel became empty",
            )
        except discord.NotFound:
            pass
        except discord.Forbidden:
            print(
                "[temporary_voice] Missing Manage Channels permission "
                "while deleting a temporary room."
            )
        except discord.HTTPException as error:
            print(
                "[temporary_voice] Failed to delete temporary room: "
                f"{error}"
            )

    # ============================================================ Voice events

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return

        guild = member.guild

        if member.bot:
            return

        # Create a room only when Temporary Voice is enabled and the
        # member joins the configured join-to-create channel.
        if self._is_enabled(guild.id):
            join_channel_id = self._get_join_channel_id(guild.id)
            category_id = self._get_category_id(guild.id)

            if (
                after.channel is not None
                and after.channel.id == join_channel_id
                and category_id is not None
            ):
                category = guild.get_channel(category_id)

                if not isinstance(category, discord.CategoryChannel):
                    print(
                        "[temporary_voice] The configured category is "
                        f"invalid for {guild.name} ({guild.id})."
                    )
                    return

                lock = self._get_creation_lock(guild.id)

                async with lock:
                    # Member may have moved before the lock became free.
                    current_voice = member.voice.channel if member.voice else None

                    if (
                        current_voice is None
                        or current_voice.id != join_channel_id
                    ):
                        return

                    room = await self._create_room(member, category)

                    if room is None:
                        return

                    self.bot.add_view(
                        VoiceControlView(
                            cog=self,
                            voice_channel_id=room.id,
                        )
                    )

                    await self._send_control_panel(room)

                    try:
                        await member.move_to(
                            room,
                            reason="Moving member to temporary voice room",
                        )
                    except discord.Forbidden:
                        print(
                            "[temporary_voice] Missing Move Members "
                            f"permission in {guild.name} ({guild.id})."
                        )
                    except discord.HTTPException as error:
                        print(
                            "[temporary_voice] Failed to move member "
                            f"into a temporary room: {error}"
                        )

        # Delete a tracked temporary room after its last member leaves.
        if (
            before.channel is not None
            and isinstance(before.channel, discord.VoiceChannel)
            and self._is_temporary_room(guild.id, before.channel.id)
        ):
            old_channel = before.channel

            await asyncio.sleep(2)

            if not old_channel.members:
                await self._delete_room(old_channel)


class VoiceControlView(discord.ui.View):
    """Persistent lock and invite controls for one temporary room."""

    def __init__(
        self,
        cog: TemporaryVoice,
        voice_channel_id: int,
    ):
        super().__init__(timeout=None)

        self.cog = cog
        self.voice_channel_id = voice_channel_id

        room = self.cog._get_room_from_any_guild(voice_channel_id)
        locked = bool(room.get("locked", False)) if room else False

        self.lock_button = LockButton(self, locked=locked)
        self.invite_button = InviteButton(self)

        self.add_item(self.lock_button)
        self.add_item(self.invite_button)

    def update_lock_button(self, locked: bool) -> None:
        if locked:
            self.lock_button.label = "Unlock"
            self.lock_button.emoji = "🔓"
            self.lock_button.style = discord.ButtonStyle.success
        else:
            self.lock_button.label = "Lock"
            self.lock_button.emoji = "🔒"
            self.lock_button.style = discord.ButtonStyle.danger


class LockButton(discord.ui.Button):
    def __init__(
        self,
        view: VoiceControlView,
        *,
        locked: bool,
    ):
        super().__init__(
            label="Unlock" if locked else "Lock",
            emoji="🔓" if locked else "🔒",
            style=(
                discord.ButtonStyle.success
                if locked
                else discord.ButtonStyle.danger
            ),
            custom_id=f"vc_lock:{view.voice_channel_id}",
        )
        self.control_view = view

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.control_view
        voice_channel = await view.cog._owner_only(interaction)

        if voice_channel is None or interaction.guild is None:
            return

        room = view.cog._get_room(
            interaction.guild.id,
            voice_channel.id,
        )

        if room is None:
            await interaction.response.send_message(
                "This temporary room is no longer tracked.",
                ephemeral=True,
            )
            return

        locked = bool(room.get("locked", False))
        new_locked = not locked

        try:
            await voice_channel.set_permissions(
                interaction.guild.default_role,
                connect=not new_locked,
            )

            await voice_channel.set_permissions(
                interaction.user,
                view_channel=True,
                connect=True,
                speak=True,
                send_messages=True,
                read_message_history=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to update this room's permissions.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord returned an error while updating the room.",
                ephemeral=True,
            )
            return

        room["locked"] = new_locked
        view.cog._set_room(
            interaction.guild.id,
            voice_channel.id,
            room,
        )
        view.update_lock_button(new_locked)

        content = (
            "Your voice channel is now locked. "
            "Use **Invite** to allow a member to join."
            if new_locked
            else "Your voice channel is now unlocked."
        )

        await interaction.response.edit_message(
            content=content,
            view=view,
        )


class InviteButton(discord.ui.Button):
    def __init__(self, view: VoiceControlView):
        super().__init__(
            label="Invite",
            emoji="👤",
            style=discord.ButtonStyle.primary,
            custom_id=f"vc_invite:{view.voice_channel_id}",
        )
        self.control_view = view

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.control_view
        voice_channel = await view.cog._owner_only(interaction)

        if voice_channel is None:
            return

        select = InviteUserSelect(view)

        # Avoid duplicate selects if the owner clicks Invite repeatedly.
        for child in list(view.children):
            if isinstance(child, InviteUserSelect):
                view.remove_item(child)

        view.add_item(select)

        await interaction.response.edit_message(
            content="Select a member to invite into your voice channel.",
            view=view,
        )


class InviteUserSelect(discord.ui.UserSelect):
    def __init__(self, parent_view: VoiceControlView):
        super().__init__(
            placeholder="Select a member to invite",
            min_values=1,
            max_values=1,
            custom_id=(
                f"vc_select_invite:"
                f"{parent_view.voice_channel_id}"
            ),
        )
        self.parent_view = parent_view

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.parent_view
        voice_channel = await view.cog._owner_only(interaction)

        if voice_channel is None:
            return

        selected_user = self.values[0]

        if not isinstance(selected_user, discord.Member):
            await interaction.response.send_message(
                "That user is not a member of this server.",
                ephemeral=True,
            )
            return

        try:
            await voice_channel.set_permissions(
                selected_user,
                view_channel=True,
                connect=True,
                speak=True,
                read_message_history=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to invite that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord returned an error while inviting that member.",
                ephemeral=True,
            )
            return

        view.remove_item(self)

        await interaction.response.edit_message(
            content=(
                f"{selected_user.mention} can now join your voice channel."
            ),
            view=view,
        )


# This helper is attached after the class definition to keep the main
# class grouped by feature above.
def _get_room_from_any_guild(
    self: TemporaryVoice,
    voice_channel_id: int,
) -> Optional[dict]:
    for guild in self.bot.guilds:
        room = self._get_room(guild.id, voice_channel_id)

        if room is not None:
            return room

    return None


TemporaryVoice._get_room_from_any_guild = _get_room_from_any_guild


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TemporaryVoice(bot))