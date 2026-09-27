from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cogs.config import is_bot_owner
from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import TagStore

MODULE_KEY = "tags"
MAX_TAG_NAME_LENGTH = 50
MAX_TAG_CONTENT_LENGTH = 1900
MAX_TRUSTED_ROLES = 5


def _role_id(value: object) -> int | None:
    try:
        role_id = int(value)
    except (TypeError, ValueError):
        return None
    return role_id if role_id > 0 else None


class TagContentModal(discord.ui.Modal):
    def __init__(self, cog: Tags, name: str, *, editing: bool):
        super().__init__(title=f"{'Edit' if editing else 'Create'} tag: {name}")
        self.cog = cog
        self.name = name
        self.editing = editing
        self.content = discord.ui.TextInput(
            label="Tag content",
            style=discord.TextStyle.paragraph,
            placeholder="Enter the plain-text message to save...",
            max_length=MAX_TAG_CONTENT_LENGTH,
            required=True,
        )
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tags can only be used inside a server.", ephemeral=True
            )
            return

        content = self.content.value.strip()
        if not content:
            await interaction.response.send_message(
                "Tag content cannot be empty.", ephemeral=True
            )
            return

        if self.editing:
            changed = self.cog.tags.update(
                interaction.guild.id,
                self.name,
                content,
                interaction.user.id,
            )
            if not changed:
                await interaction.response.send_message(
                    f'Tag "{self.name}" was not found.', ephemeral=True
                )
                return
            await interaction.response.send_message(
                "✅ I successfully updated that tag!", ephemeral=True
            )
            return

        created = self.cog.tags.create(
            interaction.guild.id,
            self.name,
            content,
            interaction.user.id,
        )
        if not created:
            await interaction.response.send_message(
                f'The tag "{self.name}" already exists. Use `/tag edit` to change it.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f'✅ Successfully created the tag "{self.name}"!', ephemeral=True
        )


class Tags(commands.Cog):
    tag = app_commands.Group(name="tag", description="Create and use reusable plain-text messages.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.tags = TagStore(DB_PATH)

    def _trusted_role_ids(self, guild_id: int) -> set[int]:
        role_ids: set[int] = set()
        for index in range(1, MAX_TRUSTED_ROLES + 1):
            role_id = _role_id(
                self.store.get(guild_id, MODULE_KEY, f"trusted_role_{index}")
            )
            if role_id is not None:
                role_ids.add(role_id)
        return role_ids

    def _can_manage_tags(self, member: discord.Member) -> bool:
        if is_bot_owner(member):
            return True
        configured_role_ids = {
            _role_id(self.store.get(member.guild.id, "moderation", "mod_role")),
            _role_id(self.store.get(member.guild.id, "moderation", "head_mod_role")),
            _role_id(self.store.get(member.guild.id, "report_msg", "staff_role")),
            *self._trusted_role_ids(member.guild.id),
        }
        configured_role_ids.discard(None)
        return any(role.id in configured_role_ids for role in member.roles)

    def _enabled(self, guild_id: int) -> bool:
        return bool(self.store.get(guild_id, MODULE_KEY, "enabled", True))

    async def tag_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []

        normalized_current = self.tags.normalize_name(current)
        names = self.tags.list(interaction.guild.id)
        return [
            app_commands.Choice(name=name[:100], value=name)
            for name in names
            if not normalized_current or normalized_current in name
        ][:25]

    async def _require_manager(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used inside a server.", ephemeral=True
            )
            return False
        if not self._enabled(interaction.guild.id):
            await interaction.response.send_message(
                "Tags are disabled in this server.", ephemeral=True
            )
            return False
        if not self._can_manage_tags(interaction.user):
            await interaction.response.send_message(
                "You need a configured moderator, staff, or trusted role to manage tags.",
                ephemeral=True,
            )
            return False
        return True

    @tag.command(name="create", description="Create a reusable plain-text tag.")
    @app_commands.describe(name="Tag name to create.")
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._require_manager(interaction):
            return
        name = self.tags.normalize_name(name)
        if not name or len(name) > MAX_TAG_NAME_LENGTH or "\n" in name:
            await interaction.response.send_message(
                "Tag names must be 1 to 50 characters and fit on one line.", ephemeral=True
            )
            return
        await interaction.response.send_modal(TagContentModal(self, name, editing=False))

    @tag.command(name="edit", description="Edit an existing plain-text tag.")
    @app_commands.describe(name="Tag name to edit.")
    @app_commands.autocomplete(name=tag_autocomplete)
    async def edit(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._require_manager(interaction):
            return
        name = self.tags.normalize_name(name)
        if self.tags.get(interaction.guild.id, name) is None:
            await interaction.response.send_message(
                f'Tag "{name}" was not found.', ephemeral=True
            )
            return
        await interaction.response.send_modal(TagContentModal(self, name, editing=True))

    @tag.command(name="view", description="Send a saved tag as plain text.")
    @app_commands.describe(
        name="Tag name to send.",
        target="Optional user to mention on a new final line.",
    )
    @app_commands.autocomplete(name=tag_autocomplete)
    async def view(
        self,
        interaction: discord.Interaction,
        name: str,
        target: discord.Member | None = None,
    ) -> None:
        if not await self._require_manager(interaction):
            return
        tag = self.tags.get(interaction.guild.id, name)
        if tag is None:
            await interaction.response.send_message(
                f'Tag "{self.tags.normalize_name(name)}" was not found.', ephemeral=True
            )
            return
        content = str(tag["content"])
        if target is not None:
            content = f"{content}\n{target.mention}"
        await interaction.response.send_message(
            content,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @tag.command(name="delete", description="Delete a saved tag.")
    @app_commands.describe(name="Tag name to delete.")
    @app_commands.autocomplete(name=tag_autocomplete)
    async def delete(self, interaction: discord.Interaction, name: str) -> None:
        if not await self._require_manager(interaction):
            return
        if not self.tags.delete(interaction.guild.id, name):
            await interaction.response.send_message(
                f'Tag "{self.tags.normalize_name(name)}" was not found.', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f'✅ Successfully deleted the tag "{self.tags.normalize_name(name)}"!',
            ephemeral=True,
        )

    @tag.command(name="list", description="List saved tags.")
    async def list(self, interaction: discord.Interaction) -> None:
        if not await self._require_manager(interaction):
            return
        names = self.tags.list(interaction.guild.id)
        await interaction.response.send_message(
            "Available tags: " + (", ".join(names) if names else "none"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tags(bot))
