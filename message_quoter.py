# cogs/message_quoter.py
from __future__ import annotations


import json
import re
import sqlite3
import traceback
from pathlib import Path
from typing import Optional


import discord
from discord.ext import commands



NO_MENTIONS = discord.AllowedMentions.none()


MESSAGE_LINK_RE = re.compile(
    r"https?://(?:[a-zA-Z0-9-]+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)"
)
BARE_EMOJI_ID_RE = re.compile(r"(?<!\w):([0-9]{9,20}):(?!\w)")


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "bot.db"


MODULE_KEY = "message_quoter"
MAX_ALLOWED_ROLES = 10



class MessageQuoterConfig:
    """Reads Message Quoter settings saved by the setup dashboard."""


    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path


    def get(self, guild_id: int, key: str, default=None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT value
                    FROM guild_setup_config
                    WHERE guild_id = ? AND module = ? AND key = ?
                    """,
                    (guild_id, MODULE_KEY, key),
                ).fetchone()


            if row is None:
                return default


            return json.loads(row[0])


        except (sqlite3.Error, json.JSONDecodeError, TypeError) as error:
            print(f"[message_quoter] Config read error: {error}")
            return default


    def is_enabled(self, guild_id: int) -> bool:
        return bool(self.get(guild_id, "enabled", False))


    def require_reply(self, guild_id: int) -> bool:
        return bool(self.get(guild_id, "require_reply", True))


    def embed_color(self, guild_id: int) -> Optional[discord.Color]:
        raw_color = self.get(guild_id, "embed_color", "96edf1")


        if not raw_color:
            return None


        try:
            hex_color = str(raw_color).strip().lstrip("#")
            return discord.Color(int(hex_color, 16))
        except (TypeError, ValueError):
            return discord.Color(0x96EDF1)


    def get_allowed_role_ids(self, guild_id: int) -> list[int]:
        """Return the configured Message Quoter role IDs (up to 10)."""
        role_ids: list[int] = []

        for index in range(1, MAX_ALLOWED_ROLES + 1):
            role_id = self.get(guild_id, f"allowed_role_{index}", None)

            if role_id is None:
                continue

            try:
                role_ids.append(int(role_id))
            except (TypeError, ValueError):
                continue

        return role_ids



class MessageQuoter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = MessageQuoterConfig()


    @staticmethod
    def normalize_custom_emojis(
        content: str,
        guild: Optional[discord.Guild],
    ) -> str:
        """Convert bare custom emoji IDs to Discord's renderable emoji format."""
        if guild is None or not content:
            return content

        emojis_by_id = {emoji.id: emoji for emoji in guild.emojis}

        def replace_emoji(match: re.Match[str]) -> str:
            emoji = emojis_by_id.get(int(match.group(1)))
            return str(emoji) if emoji is not None else match.group(0)

        return BARE_EMOJI_ID_RE.sub(replace_emoji, content)


    def get_embed_color(
        self,
        guild_id: int,
        quoted: discord.Message,
    ) -> discord.Color:
        """Use the configured server colour, then author colour as fallback."""
        configured_color = self.config.embed_color(guild_id)
        if configured_color is not None:
            return configured_color


        if isinstance(quoted.author, discord.Member):
            return quoted.author.color


        return discord.Color.blurple()


    async def quote_message(
        self,
        destination: discord.abc.Messageable,
        quoted: discord.Message,
    ) -> None:
        """Send a quoted message as one or more embeds."""
        guild_id = quoted.guild.id if quoted.guild else None


        if guild_id is not None:
            embed_color = self.get_embed_color(guild_id, quoted)
        elif isinstance(quoted.author, discord.Member):
            embed_color = quoted.author.color
        else:
            embed_color = discord.Color.blurple()


        image_attachments = []
        other_attachments = []


        for attachment in quoted.attachments:
            is_image = (
                attachment.content_type
                and attachment.content_type.startswith("image")
            ) or (
                attachment.filename
                and attachment.filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                )
            )


            if is_image:
                image_attachments.append(attachment)
            else:
                other_attachments.append(attachment)


        has_text = bool(quoted.content)
        has_image = bool(image_attachments)


        guild_icon_url = None
        if quoted.guild and quoted.guild.icon:
            guild_icon_url = quoted.guild.icon.url


        if has_text or has_image:
            embed = discord.Embed(
                description=(
                    self.normalize_custom_emojis(quoted.content, quoted.guild)
                    if has_text
                    else None
                ),
                color=embed_color,
                timestamp=quoted.created_at,
            )
            embed.set_author(
                name=str(quoted.author),
                icon_url=quoted.author.display_avatar.url,
                url=quoted.jump_url,
            )
            embed.set_footer(
                text=f"#{quoted.channel.name}",
                icon_url=guild_icon_url,
            )


            if has_image:
                embed.set_image(url=image_attachments[0].url)


            await destination.send(embed=embed, allowed_mentions=NO_MENTIONS)


            for image in image_attachments[1:]:
                image_embed = discord.Embed(color=embed_color)
                image_embed.set_author(
                    name=str(quoted.author),
                    icon_url=quoted.author.display_avatar.url,
                    url=quoted.jump_url,
                )
                image_embed.set_image(url=image.url)
                image_embed.set_footer(
                    text=f"#{quoted.channel.name}",
                    icon_url=guild_icon_url,
                )
                await destination.send(
                    embed=image_embed,
                    allowed_mentions=NO_MENTIONS,
                )


        elif quoted.embeds:
            for original_embed in quoted.embeds:
                await destination.send(
                    embed=original_embed,
                    allowed_mentions=NO_MENTIONS,
                )


        for attachment in other_attachments:
            await destination.send(
                attachment.url,
                allowed_mentions=NO_MENTIONS,
            )


        if quoted.stickers:
            names = ", ".join(sticker.name for sticker in quoted.stickers)
            await destination.send(
                f"Sticker(s): {names}",
                allowed_mentions=NO_MENTIONS,
            )


    def can_use_quoter(self, message: discord.Message) -> bool:
        """Return whether the author is allowed to use the Message Quoter."""
        if not isinstance(message.author, discord.Member):
            return False

        allowed_role_ids = self.config.get_allowed_role_ids(message.guild.id)

        # No roles configured means the feature remains available to everyone.
        if not allowed_role_ids:
            return True

        return any(
            role.id in allowed_role_ids
            for role in message.author.roles
        )


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Quote Discord message links when Message Quoter is enabled."""
        if message.guild is None:
            return


        # Keep this enabled unless you deliberately want other bots'
        # messages to trigger quotes.
        if message.author.bot:
            return


        guild_id = message.guild.id


        # Setup Dashboard: Message Quoter > Enabled
        if not self.config.is_enabled(guild_id):
            return


        # Setup Dashboard: Message Quoter > Allowed roles
        if not self.can_use_quoter(message):
            return


        # Setup Dashboard: Message Quoter > Require reply
        if self.config.require_reply(guild_id):
            if message.reference is None:
                return


        matches = MESSAGE_LINK_RE.findall(message.content)
        if not matches:
            return


        seen_message_ids = set()


        for _link_guild_id, channel_id_str, message_id_str in matches:
            if message_id_str in seen_message_ids:
                continue


            seen_message_ids.add(message_id_str)


            try:
                channel_id = int(channel_id_str)
                message_id = int(message_id_str)


                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    continue


                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    continue


                try:
                    quoted = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden):
                    continue


                await self.quote_message(message.channel, quoted)


            except Exception as error:
                print(f"[message_quoter] Error processing message link: {error}")
                traceback.print_exc()



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageQuoter(bot))