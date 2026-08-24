from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands
from cogs.config import BOT_OWNER_ID, is_bot_owner

# Shared setup store (written by the /setup dashboard). setup_ui is
# loaded before this cog in bot.EXTENSIONS, so the import is safe.
from cogs.setup_ui import SetupConfigStore, DB_PATH


MODULE_KEY = "reaction_roles"



class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)


    # --------------------------------------------------
    # Store helpers
    # --------------------------------------------------


    def _is_enabled(self, guild_id: int) -> bool:
        return bool(self.store.get(guild_id, MODULE_KEY, "enabled", default=False))


    def _get_channel_id(self, guild_id: int) -> Optional[int]:
        value = self.store.get(guild_id, MODULE_KEY, "channel")
        return int(value) if value is not None else None


    def _get_roles(self, guild_id: int) -> Dict[str, int]:
        """Return the {emoji: role_id} mapping for a guild."""
        value = self.store.get(guild_id, MODULE_KEY, "roles")

        if not isinstance(value, dict):
            return {}

        roles: Dict[str, int] = {}

        for emoji, role_id in value.items():
            try:
                roles[str(emoji)] = int(role_id)
            except (TypeError, ValueError):
                continue

        return roles


    def _set_roles(self, guild_id: int, roles: Dict[str, int]) -> None:
        self.store.set(guild_id, MODULE_KEY, "roles", roles)


    def _get_message_id(self, guild_id: int) -> Optional[int]:
        value = self.store.get(guild_id, MODULE_KEY, "message_id")
        return int(value) if value is not None else None


    def _set_message_id(self, guild_id: int, message_id: Optional[int]) -> None:
        self.store.set(guild_id, MODULE_KEY, "message_id", message_id)


    # --------------------------------------------------
    # Embed builder
    # --------------------------------------------------


    def _build_reaction_embed(
        self,
        guild: discord.Guild,
        roles: Dict[str, int],
    ) -> discord.Embed:
        lines = ["React below to select your roles:", ""]

        for emoji, role_id in roles.items():
            role = guild.get_role(role_id)
            lines.append(
                f"{emoji} → {role.mention if role else f'Missing role `{role_id}`'}"
            )

        embed = discord.Embed(
            title="Choose your roles",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Remove your reaction to remove the corresponding role.")

        return embed


    async def _refresh_reaction_message(self, guild: discord.Guild) -> None:
        """
        Update the existing reaction message's embed so it reflects the
        current emoji->role pairs. Used after add/remove.
        """
        channel_id = self._get_channel_id(guild.id)
        message_id = self._get_message_id(guild.id)

        if channel_id is None or message_id is None:
            return

        channel = guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=self._build_reaction_embed(guild, self._get_roles(guild.id))
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


    # --------------------------------------------------
    # Reaction handling
    # --------------------------------------------------


    async def update_reaction_role(
        self,
        payload: discord.RawReactionActionEvent,
        add_role: bool,
    ) -> None:
        if (
            payload.guild_id is None
            or (self.bot.user is not None and payload.user_id == self.bot.user.id)
        ):
            return

        guild_id = payload.guild_id

        # Master switch: do nothing if reaction roles are disabled.
        if not self._is_enabled(guild_id):
            return

        message_id = self._get_message_id(guild_id)

        if message_id is None or payload.message_id != message_id:
            return

        role_id = self._get_roles(guild_id).get(payload.emoji.name)

        if role_id is None:
            return

        guild = self.bot.get_guild(guild_id)

        if guild is None:
            return

        role = guild.get_role(role_id)

        if role is None:
            return

        member = guild.get_member(payload.user_id)

        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if member.bot:
            return

        action, reason = (
            (member.add_roles, "Reaction role added")
            if add_role
            else (member.remove_roles, "Reaction role removed")
        )

        try:
            await action(role, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.update_reaction_role(payload, add_role=True)


    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.update_reaction_role(payload, add_role=False)


    # --------------------------------------------------
    # Setup command: (re)post the reaction message
    # --------------------------------------------------


    @app_commands.command(
        name="setup_reactions",
        description="(Re)post the reaction-role message in the configured channel.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setup_reactions_slash(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            return

        if not self._is_enabled(guild.id):
            await interaction.response.send_message(
                "Reaction roles are disabled. Enable them in /setup first.",
                ephemeral=True,
            )
            return

        channel_id = self._get_channel_id(guild.id)

        if channel_id is None:
            await interaction.response.send_message(
                "No reaction channel is configured. Set one in /setup first.",
                ephemeral=True,
            )
            return

        channel = guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "The configured channel is invalid or no longer a text channel.",
                ephemeral=True,
            )
            return

        roles = self._get_roles(guild.id)

        if not roles:
            await interaction.response.send_message(
                "No reaction roles are configured yet. "
                "Add some with `/reaction_role_add` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Delete a previous reaction message if one exists.
        old_message_id = self._get_message_id(guild.id)

        if old_message_id is not None:
            try:
                old_message = await channel.fetch_message(old_message_id)
                await old_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        embed = self._build_reaction_embed(guild, roles)

        try:
            reaction_message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Could not send the reaction-role message.",
                ephemeral=True,
            )
            return

        self._set_message_id(guild.id, reaction_message.id)

        failed = []

        for emoji in roles:
            try:
                await reaction_message.add_reaction(emoji)
            except (discord.Forbidden, discord.HTTPException):
                failed.append(emoji)

        if failed:
            await interaction.followup.send(
                "Reaction-role message created in "
                f"{channel.mention}, but these reactions failed: "
                f"{', '.join(failed)}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Reaction-role message created in {channel.mention} "
                f"with ID `{reaction_message.id}`.",
                ephemeral=True,
            )


    @setup_reactions_slash.error
    async def setup_reactions_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the Manage Roles permission to use this command."
        else:
            print(f"[ReactionRoles] setup_reactions error: {error}")
            message = "An error occurred while setting up reaction roles."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


    # --------------------------------------------------
    # Reaction role management commands
    # --------------------------------------------------


    @app_commands.command(
        name="reaction_role_add",
        description="Add an emoji -> role pair to the reaction-role menu.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.describe(
        emoji="The emoji members react with (e.g. 🔥 or a custom emoji).",
        role="The role granted by that reaction.",
    )
    async def reaction_role_add(
        self,
        interaction: discord.Interaction,
        emoji: str,
        role: discord.Role,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            return

        if role.is_default():
            await interaction.response.send_message(
                "You cannot use @everyone as a reaction role.",
                ephemeral=True,
            )
            return

        if role.managed:
            await interaction.response.send_message(
                "You cannot use a managed integration role.",
                ephemeral=True,
            )
            return

        if guild.me is not None and role >= guild.me.top_role:
            await interaction.response.send_message(
                "I cannot assign that role -- it is at or above my top role.",
                ephemeral=True,
            )
            return

        emoji = emoji.strip()
        roles = self._get_roles(guild.id)
        roles[emoji] = role.id
        self._set_roles(guild.id, roles)

        # Refresh the existing message's embed so the new pair shows up.
        await self._refresh_reaction_message(guild)

        await interaction.response.send_message(
            f"Added {emoji} → {role.mention} to the reaction-role menu.\n"
            "Run `/setup_reactions` to (re)post the message with reactions.",
            ephemeral=True,
        )


    @app_commands.command(
        name="reaction_role_remove",
        description="Remove an emoji -> role pair from the reaction-role menu.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.describe(
        emoji="The emoji to remove from the menu.",
    )
    async def reaction_role_remove(
        self,
        interaction: discord.Interaction,
        emoji: str,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            return

        emoji = emoji.strip()
        roles = self._get_roles(guild.id)

        if emoji not in roles:
            await interaction.response.send_message(
                f"{emoji} is not in the reaction-role menu.",
                ephemeral=True,
            )
            return

        removed_role_id = roles.pop(emoji)
        self._set_roles(guild.id, roles)

        await self._refresh_reaction_message(guild)

        removed_role = guild.get_role(removed_role_id)
        removed_text = removed_role.mention if removed_role else f"`{removed_role_id}`"

        await interaction.response.send_message(
            f"Removed {emoji} → {removed_text} from the reaction-role menu.",
            ephemeral=True,
        )


    @app_commands.command(
        name="reaction_role_list",
        description="List the current emoji -> role pairs for this server.",
    )
    @app_commands.guild_only()
    async def reaction_role_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild

        if guild is None:
            return

        roles = self._get_roles(guild.id)

        if not roles:
            await interaction.response.send_message(
                "No reaction roles are configured. "
                "Use `/reaction_role_add` to add some.",
                ephemeral=True,
            )
            return

        lines = []

        for emoji, role_id in roles.items():
            role = guild.get_role(role_id)
            lines.append(
                f"{emoji} → {role.mention if role else f'Missing role `{role_id}`'}"
            )

        embed = discord.Embed(
            title="Reaction roles",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


    @reaction_role_add.error
    @reaction_role_remove.error
    async def reaction_role_management_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the Manage Roles permission to manage reaction roles."
        else:
            print(f"[ReactionRoles] management error: {error}")
            message = "An error occurred while updating reaction roles."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))