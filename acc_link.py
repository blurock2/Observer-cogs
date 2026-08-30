from __future__ import annotations

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request
import xml.etree.ElementTree as ET

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore

EMBED_COLOR = 0x96EDF1
MODULE_KEY = "acc_link"
ACCOUNT_DB_PATH = Path("data/account_links.sqlite3")


class AccountLinkStore:
    """Store a Discord member's linked GitHub or Steam account."""

    def __init__(self, db_path: str | Path = ACCOUNT_DB_PATH) -> None:
        self.db_path = str(db_path)
        ACCOUNT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_links (
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    username TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (user_id, platform)
                )
                """
            )

    def set_link(self, user_id: int, platform: str, username: str) -> None:
        normalized = platform.lower()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_links (user_id, platform, username, updated_at)
                VALUES (?, ?, ?, strftime('%s','now'))
                ON CONFLICT(user_id, platform)
                DO UPDATE SET username = excluded.username,
                              updated_at = excluded.updated_at
                """,
                (user_id, normalized, username),
            )

    def get_link(self, user_id: int, platform: str) -> str | None:
        normalized = platform.lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username FROM account_links WHERE user_id = ? AND platform = ?",
                (user_id, normalized),
            ).fetchone()
        return row["username"] if row else None

    def get_links(self, user_id: int) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT platform, username FROM account_links WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {row["platform"]: row["username"] for row in rows}

    def find_claim_by_username(self, platform: str, username: str) -> tuple[int | None, str | None]:
        normalized_platform = platform.lower()
        cleaned = (username or "").strip()
        if not cleaned:
            return None, None

        candidates: set[str] = {cleaned.lower()}

        if normalized_platform == "github":
            parsed = urlparse.urlparse(cleaned)
            if parsed.netloc.lower() in {"github.com", "www.github.com"}:
                path = parsed.path.strip("/")
                if path:
                    candidates.add(path.lower())
            if cleaned.startswith("https://") or cleaned.startswith("http://"):
                candidates.add(cleaned.rstrip("/").lower())

        if normalized_platform == "steam":
            if cleaned.startswith("https://") or cleaned.startswith("http://"):
                parsed = urlparse.urlparse(cleaned)
                candidates.add(parsed.path.strip("/").lower())
                if "/id/" in parsed.path.lower():
                    candidates.add(parsed.path.split("/id/", 1)[1].strip("/").lower())
                elif "/profiles/" in parsed.path.lower():
                    candidates.add(parsed.path.split("/profiles/", 1)[1].strip("/").lower())
                candidates.add(cleaned.rstrip("/").lower())
            target = AccountLink._steam_target_from_input(cleaned)
            if target:
                candidates.add(target.lower())
                candidates.add(f"https://steamcommunity.com/id/{target}".lower())
                candidates.add(f"https://steamcommunity.com/profiles/{target}".lower())

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, username FROM account_links WHERE platform = ?",
                (normalized_platform,),
            ).fetchall()

        for row in rows:
            stored = (row["username"] or "").strip()
            stored_variants: set[str] = {stored.lower()}
            if normalized_platform == "steam":
                stored_variants.add(AccountLink._steam_target_from_input(stored).lower())
                if stored.startswith("https://") or stored.startswith("http://"):
                    parsed = urlparse.urlparse(stored)
                    stored_variants.add(parsed.path.strip("/").lower())
                    if "/id/" in parsed.path.lower():
                        stored_variants.add(parsed.path.split("/id/", 1)[1].strip("/").lower())
                    if "/profiles/" in parsed.path.lower():
                        stored_variants.add(parsed.path.split("/profiles/", 1)[1].strip("/").lower())
            if normalized_platform == "github":
                if stored.startswith("https://") or stored.startswith("http://"):
                    parsed = urlparse.urlparse(stored)
                    stored_variants.add(parsed.path.strip("/").lower())
            if candidates & stored_variants:
                return int(row["user_id"]), row["username"]

        return None, None


class AccountLink(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = AccountLinkStore()
        self.config = SetupConfigStore(DB_PATH)

    def _is_enabled(self, guild_id: int | None) -> bool:
        if guild_id is None:
            return True
        return bool(self.config.get(guild_id, MODULE_KEY, "enabled", default=False))

    def _channel_allowed(self, guild_id: int | None, channel_id: int | None) -> bool:
        if guild_id is None:
            return True

        configured = self.config.get(guild_id, MODULE_KEY, "channel")
        if configured in (None, "", 0, False):
            return True

        try:
            required_channel_id = int(configured)
        except (TypeError, ValueError):
            return True

        if channel_id is None:
            return False

        return int(channel_id) == required_channel_id

    async def _require_enabled(
        self,
        guild_id: int | None,
        channel_id: int | None = None,
    ) -> bool:
        if not self._is_enabled(guild_id):
            return False

        if not self._channel_allowed(guild_id, channel_id):
            return False

        return True

    @staticmethod
    def _embed_error(message: str) -> discord.Embed:
        return discord.Embed(
            description=f"⚠️ {message}",
            color=EMBED_COLOR,
        )

    @staticmethod
    def _user_agent() -> dict[str, str]:
        return {"User-Agent": "ObserverBot/1.0 (Discord bot)"}

    @staticmethod
    def _safe_urlopen(url: str) -> str | None:
        req = request.Request(url, headers=AccountLink._user_agent())
        try:
            with request.urlopen(req, timeout=10) as response:
                return response.read().decode("utf-8", errors="ignore")
        except (urlerror.URLError, TimeoutError, ValueError):
            return None

    @staticmethod
    def _github_profile_url(username: str) -> str:
        return f"https://github.com/{username.strip()}"

    @staticmethod
    def _steam_profile_url(value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return "https://steamcommunity.com"
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned
        return f"https://steamcommunity.com/id/{urlparse.quote(cleaned)}"

    @staticmethod
    def _steam_target_from_input(value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return ""
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            parsed = urlparse.urlparse(cleaned)
            if "/id/" in parsed.path.lower():
                return parsed.path.split("/id/", 1)[1].strip("/")
            if "/profiles/" in parsed.path.lower():
                return parsed.path.split("/profiles/", 1)[1].strip("/")
            return parsed.path.strip("/")
        if cleaned.isdigit():
            return cleaned
        return cleaned

    @staticmethod
    def _normalize_platform(value: str) -> str:
        key = (value or "").strip().lower()
        if key in {"github", "gh"}:
            return "github"
        if key in {"steam", "ste"}:
            return "steam"
        raise commands.BadArgument("Platform must be `github` or `steam`.")

    @staticmethod
    def _sanitize_steam_description(value: str | None) -> str:
        if not value:
            return "No profile summary available."

        cleaned = html.unescape(value)
        cleaned = cleaned.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or "No profile summary available."

    @staticmethod
    def _steam_embed_data(profile_data: dict[str, Any]) -> dict[str, Any]:
        avatar = profile_data.get("avatar_full") or profile_data.get("avatar")
        name = profile_data.get("personaname") or profile_data.get("steamid") or "Steam profile"
        profile_url = profile_data.get("profile_url") or "https://steamcommunity.com"
        summary = AccountLink._sanitize_steam_description(profile_data.get("summary"))
        location = profile_data.get("loccountrycode") or "Unknown location"
        status = profile_data.get("personastate")
        if status == "0":
            status_text = "Offline"
        elif status == "1":
            status_text = "Online"
        elif status == "2":
            status_text = "Busy"
        elif status == "3":
            status_text = "Away"
        elif status == "4":
            status_text = "Snooze"
        elif status == "5":
            status_text = "Looking to trade"
        elif status == "6":
            status_text = "Looking to play"
        else:
            status_text = "Status unavailable"

        return {
            "title": name,
            "url": profile_url,
            "avatar": avatar,
            "description": summary,
            "status": status_text,
            "location": location,
        }

    @staticmethod
    def _github_embed_data(profile_data: dict[str, Any]) -> dict[str, Any]:
        username = profile_data.get("login") or "github-user"
        profile_url = profile_data.get("html_url") or AccountLink._github_profile_url(username)
        display_name = profile_data.get("name") or username
        bio = profile_data.get("bio") or "No bio provided."
        location = profile_data.get("location") or "Unknown location"
        public_repos = profile_data.get("public_repos", 0)
        followers = profile_data.get("followers", 0)
        following = profile_data.get("following", 0)
        avatar = profile_data.get("avatar_url")

        return {
            "title": display_name,
            "url": profile_url,
            "avatar": avatar,
            "description": bio,
            "username": username,
            "location": location,
            "repos": public_repos,
            "followers": followers,
            "following": following,
        }

    @staticmethod
    def _build_profile_embed(platform: str, payload: dict[str, Any], claimed_by: discord.Member | discord.User | None = None) -> discord.Embed:
        color = discord.Color(EMBED_COLOR)
        embed = discord.Embed(color=color)
        embed.set_author(
            name=payload["title"],
            url=payload["url"],
            icon_url=payload.get("avatar") or None,
        )
        embed.description = payload.get("description") or "No description available."

        if platform == "github":
            embed.title = f"GitHub • {payload['username']}"
            embed.url = payload["url"]
            embed.set_thumbnail(url=payload.get("avatar") or None)
            embed.add_field(name="Location", value=payload.get("location") or "Unknown", inline=True)
            embed.add_field(name="Repos", value=str(payload.get("repos", 0)), inline=True)
            embed.add_field(name="Followers", value=str(payload.get("followers", 0)), inline=True)
            embed.add_field(name="Following", value=str(payload.get("following", 0)), inline=True)
        elif platform == "steam":
            embed.title = f"Steam • {payload['title']}"
            embed.url = payload["url"]
            embed.set_thumbnail(url=payload.get("avatar") or None)
            embed.add_field(name="Status", value=payload.get("status") or "Unknown", inline=True)
            embed.add_field(name="Location", value=payload.get("location") or "Unknown", inline=True)

        if claimed_by is not None:
            claim_label = getattr(claimed_by, "mention", str(claimed_by))
            embed.set_footer(text=f"Claimed: {claim_label}")
        else:
            embed.set_footer(text=f"Profile link: {payload['url']}")
        return embed

    @staticmethod
    def _fetch_github_profile(username: str) -> dict[str, Any] | None:
        cleaned = username.strip()
        if not cleaned:
            return None

        url = f"https://api.github.com/users/{urlparse.quote(cleaned)}"
        response = AccountLink._safe_urlopen(url)
        if response is None:
            return None

        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict) or "login" not in payload:
            return None

        return payload

    @staticmethod
    def _fetch_steam_profile(username: str) -> dict[str, Any] | None:
        target = AccountLink._steam_target_from_input(username)
        if not target:
            return None

        urls = [
            f"https://steamcommunity.com/profiles/{target}?xml=1",
            f"https://steamcommunity.com/id/{target}?xml=1",
        ]

        for url in urls:
            response = AccountLink._safe_urlopen(url)
            if response is None or "<profile>" not in response.lower():
                continue
            try:
                root = ET.fromstring(response)
            except ET.ParseError:
                continue

            steamid = root.findtext("steamID64") or root.findtext("steamID")
            personaname = root.findtext("steamID") or root.findtext("customURL") or target
            avatar = root.findtext("avatarFull") or root.findtext("avatarMedium")
            summary = root.findtext("summary") or "No profile summary available."
            profile_url = (
                f"https://steamcommunity.com/profiles/{steamid}"
                if steamid and steamid.isdigit()
                else f"https://steamcommunity.com/id/{target}"
            )

            data = {
                "steamid": steamid,
                "personaname": personaname,
                "avatar_full": avatar,
                "summary": summary,
                "profile_url": profile_url,
                "personastate": root.findtext("state") or "0",
                "loccountrycode": root.findtext("location") or "Unknown",
            }
            return data

        return None

    @staticmethod
    def _save_link_for_user(store: AccountLinkStore, user_id: int, platform: str, username: str) -> None:
        store.set_link(user_id, platform, username)

    def _resolve_member_from_string(
        self,
        guild: discord.Guild | None,
        value: str | None,
    ) -> discord.Member | None:
        if guild is None or value is None:
            return None

        cleaned = value.strip()
        if not cleaned:
            return None

        if cleaned.startswith("<@") and cleaned.endswith(">"):
            cleaned = cleaned[2:-1]
            if cleaned.startswith("!"):
                cleaned = cleaned[1:]
            try:
                user_id = int(cleaned)
            except ValueError:
                return None
            return guild.get_member(user_id)

        try:
            user_id = int(cleaned)
        except ValueError:
            pass
        else:
            return guild.get_member(user_id)

        for member in guild.members:
            if member.name.lower() == cleaned.lower():
                return member
            if member.display_name.lower() == cleaned.lower():
                return member
            if member.mention.lower() == cleaned.lower():
                return member
        return None

    def _get_linked_profile_embed(
        self,
        platform: str,
        member: discord.Member | discord.User,
    ) -> discord.Embed | None:
        saved = self.store.get_link(member.id, platform)
        if not saved:
            return None

        if platform == "steam":
            profile = self._fetch_steam_profile(saved)
            if profile is None:
                return None
            return self._build_profile_embed("steam", self._steam_embed_data(profile))

        profile = self._fetch_github_profile(saved)
        if profile is None:
            return None
        return self._build_profile_embed("github", self._github_embed_data(profile))

    @commands.hybrid_command(name="link", description="Link your GitHub or Steam account to your Discord profile.")
    @app_commands.describe(
        platform="Which account to link: github or steam.",
        username="Your public profile username or URL.",
    )
    async def link_account(
        self,
        ctx: commands.Context,
        platform: str,
        username: str,
    ) -> None:
        """Link a public GitHub or Steam profile to the caller's Discord account."""
        try:
            normalized = self._normalize_platform(platform)
        except commands.BadArgument as error:
            await ctx.reply(embed=self._embed_error(str(error)))
            return

        guild_id = getattr(ctx.guild, "id", None)
        channel_id = getattr(ctx.channel, "id", None)
        if not await self._require_enabled(guild_id, channel_id):
            if guild_id is None:
                await ctx.reply(
                    embed=self._embed_error(
                        "Account linking is disabled in this server. Enable it through `/setup` first."
                    )
                )
            else:
                configured = self.config.get(guild_id, MODULE_KEY, "channel")
                if configured not in (None, "", 0, False):
                    channel_name = self.bot.get_channel(int(configured))
                    channel_label = getattr(channel_name, "mention", f"<#{configured}>")
                    await ctx.reply(
                        embed=self._embed_error(
                            f"This command can only be used in {channel_label}."
                        )
                    )
                else:
                    await ctx.reply(
                        embed=self._embed_error(
                            "Account linking is disabled in this server. Enable it through `/setup` first."
                        )
                    )
            return

        if normalized == "github":
            profile = self._fetch_github_profile(username)
            if profile is None:
                await ctx.reply(embed=self._embed_error(f"No public GitHub profile was found for `{username}`."))
                return
            payload = self._github_embed_data(profile)
            title = payload["title"]
            profile_url = payload["url"]
            username_value = payload["username"]
        elif normalized == "steam":
            profile = self._fetch_steam_profile(username)
            if profile is None:
                await ctx.reply(embed=self._embed_error(f"No public Steam profile was found for `{username}`."))
                return
            payload = self._steam_embed_data(profile)
            title = payload["title"]
            profile_url = payload["url"]
            username_value = self._steam_target_from_input(username) or payload["title"]
            if username_value and (username.startswith("http://") or username.startswith("https://")):
                username_value = self._steam_target_from_input(username) or username_value
        self._save_link_for_user(self.store, ctx.author.id, normalized, username_value)

        embed = self._build_profile_embed(normalized, payload)
        embed.description = (
            f"✅ Linked {normalized.title()} account to your Discord profile.\n\n{embed.description}"
        )
        embed.set_footer(text=f"Linked account: {profile_url}")

        await ctx.reply(embed=embed)

    @app_commands.command(name="steam")
    @app_commands.describe(
        member="Look up a Discord member's linked Steam account.",
        username="Steam custom URL, profile ID, or profile URL to search publicly.",
    )
    async def steam_lookup(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        username: str | None = None,
    ) -> None:
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id
        if not await self._require_enabled(guild_id, channel_id):
            if guild_id is None:
                await interaction.response.send_message(
                    embed=self._embed_error(
                        "Account linking is disabled in this server. Enable it through `/setup` first."
                    ),
                    ephemeral=True,
                )
                return

            configured = self.config.get(guild_id, MODULE_KEY, "channel")
            if configured not in (None, "", 0, False):
                channel_name = self.bot.get_channel(int(configured))
                channel_label = getattr(channel_name, "mention", f"<#{configured}>")
                await interaction.response.send_message(
                    embed=self._embed_error(
                        f"This command can only be used in {channel_label}."
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=self._embed_error(
                    "Account linking is disabled in this server. Enable it through `/setup` first."
                ),
                ephemeral=True,
            )
            return

        if member is not None:
            embed = self._get_linked_profile_embed("steam", member)
            if embed is not None:
                await interaction.response.send_message(embed=embed)
                return
            await interaction.response.send_message(
                embed=self._embed_error(
                    f"{member.mention} has not linked a Steam account yet."
                )
            )
            return

        if username:
            resolved_member = self._resolve_member_from_string(interaction.guild, username)
            if resolved_member is not None:
                embed = self._get_linked_profile_embed("steam", resolved_member)
                if embed is not None:
                    await interaction.response.send_message(embed=embed)
                    return
                await interaction.response.send_message(
                    embed=self._embed_error(
                        f"{resolved_member.mention} has not linked a Steam account yet."
                    )
                )
                return

            profile = self._fetch_steam_profile(username)
            if profile is None:
                await interaction.response.send_message(
                    embed=self._embed_error(f"I could not find a public Steam profile for `{username}` and no linked Steam account was found."),
                )
                return

            claimed_user_id, _ = self.store.find_claim_by_username("steam", username)
            claimed_member = None
            if claimed_user_id is not None and interaction.guild is not None:
                claimed_member = interaction.guild.get_member(claimed_user_id)

            embed = self._build_profile_embed("steam", self._steam_embed_data(profile), claimed_member)
            if claimed_member is not None:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** {claimed_member.mention}"
                )
            elif claimed_user_id is not None:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** <@{claimed_user_id}>"
                )
            else:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** No"
                )

            await interaction.response.send_message(embed=embed)
            return

        await interaction.response.send_message(
            embed=self._embed_error("Please provide a Discord member or a Steam username to look up."),
        )

    @app_commands.command(name="github")
    @app_commands.describe(
        member="Look up a Discord member's linked GitHub account.",
        username="GitHub username to search publicly.",
    )
    async def github_lookup(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        username: str | None = None,
    ) -> None:
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id
        if not await self._require_enabled(guild_id, channel_id):
            if guild_id is None:
                await interaction.response.send_message(
                    embed=self._embed_error(
                        "Account linking is disabled in this server. Enable it through `/setup` first."
                    ),
                    ephemeral=True,
                )
                return

            configured = self.config.get(guild_id, MODULE_KEY, "channel")
            if configured not in (None, "", 0, False):
                channel_name = self.bot.get_channel(int(configured))
                channel_label = getattr(channel_name, "mention", f"<#{configured}>")
                await interaction.response.send_message(
                    embed=self._embed_error(
                        f"This command can only be used in {channel_label}."
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                embed=self._embed_error(
                    "Account linking is disabled in this server. Enable it through `/setup` first."
                ),
                ephemeral=True,
            )
            return

        if member is not None:
            embed = self._get_linked_profile_embed("github", member)
            if embed is not None:
                await interaction.response.send_message(embed=embed)
                return
            await interaction.response.send_message(
                embed=self._embed_error(
                    f"{member.mention} has not linked a GitHub account yet."
                )
            )
            return

        if username:
            resolved_member = self._resolve_member_from_string(interaction.guild, username)
            if resolved_member is not None:
                embed = self._get_linked_profile_embed("github", resolved_member)
                if embed is not None:
                    await interaction.response.send_message(embed=embed)
                    return
                await interaction.response.send_message(
                    embed=self._embed_error(
                        f"{resolved_member.mention} has not linked a GitHub account yet."
                    )
                )
                return

            profile = self._fetch_github_profile(username)
            if profile is None:
                await interaction.response.send_message(
                    embed=self._embed_error(f"I could not find a public GitHub profile for `{username}` and no linked GitHub account was found."),
                )
                return

            claimed_user_id, _ = self.store.find_claim_by_username("github", username)
            claimed_member = None
            if claimed_user_id is not None and interaction.guild is not None:
                claimed_member = interaction.guild.get_member(claimed_user_id)

            embed = self._build_profile_embed("github", self._github_embed_data(profile), claimed_member)
            if claimed_member is not None:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** {claimed_member.mention}"
                )
            elif claimed_user_id is not None:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** <@{claimed_user_id}>"
                )
            else:
                embed.description = (
                    f"{embed.description}\n\n**Claimed:** No"
                )

            await interaction.response.send_message(embed=embed)
            return

        await interaction.response.send_message(
            embed=self._embed_error("Please provide a Discord member or a GitHub username to look up."),
        )



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AccountLink(bot))
