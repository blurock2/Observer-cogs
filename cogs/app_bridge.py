from __future__ import annotations

import json
import os
import tempfile
import base64
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands, tasks

from cogs.setup_ui import DB_PATH, SetupConfigStore

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GUILDS_FILE = DATA_DIR / "app_guilds.json"
REQUESTS_FILE = DATA_DIR / "app_message_requests.json"
RESULTS_FILE = DATA_DIR / "app_message_results.json"
GITHUB_OWNER = "blurock2"
GITHUB_REPOSITORY = "my-website"
GITHUB_BRANCH = "main"
GITHUB_STATS_PATH = "observer-stats.json"


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        suffix=".tmp",
    ) as temp_file:
        json.dump(
            data,
            temp_file,
            indent=2,
            ensure_ascii=False,
        )
        temp_path = Path(temp_file.name)

    os.replace(temp_path, path)


def publish_public_stats(payload: dict[str, Any]) -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()

    if not token:
        return

    api_url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/"
        f"{GITHUB_REPOSITORY}/contents/{GITHUB_STATS_PATH}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Observer-Bot",
    }
    file_sha = None

    try:
        with urlopen(Request(api_url, headers=headers), timeout=10) as response:
            file_sha = json.loads(response.read().decode("utf-8")).get("sha")
    except HTTPError as error:
        if error.code != 404:
            print(f"[app_bridge] Could not read public stats: HTTP {error.code}")
            return
    except (OSError, URLError, json.JSONDecodeError) as error:
        print(f"[app_bridge] Could not read public stats: {error}")
        return

    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    request_payload: dict[str, Any] = {
        "message": "Update Observer live stats",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    if file_sha:
        request_payload["sha"] = file_sha

    request = Request(
        api_url,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )

    try:
        with urlopen(request, timeout=15) as response:
            if response.status not in (200, 201):
                print(f"[app_bridge] GitHub rejected public stats: HTTP {response.status}")
    except (HTTPError, OSError, URLError) as error:
        print(f"[app_bridge] Could not publish public stats: {error}")


class AppBridge(commands.Cog):
    """
    Local bridge between Observer Bot Manager and the running bot.

    It writes live guild data for the desktop UI and receives message
    requests from that UI. It is intentionally local-file based, so the
    manager does not need the Discord token.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = datetime.now(UTC)
        self.commands_run = 0

        DATA_DIR.mkdir(exist_ok=True)

        self.process_message_requests.start()
        self.refresh_guild_data.start()
        self.publish_stats.start()

    def cog_unload(self) -> None:
        self.process_message_requests.cancel()
        self.refresh_guild_data.cancel()
        self.publish_stats.cancel()

    def write_public_stats(self) -> None:
        member_count = sum(
            guild.member_count or 0
            for guild in self.bot.guilds
        )
        payload = {
            "status": "Online",
            "servers": len(self.bot.guilds),
            "users": member_count,
            "commands_run": self.commands_run,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        publish_public_stats(payload)

    def write_guild_data(self) -> None:
        guilds: list[dict[str, Any]] = []

        for guild in sorted(
            self.bot.guilds,
            key=lambda item: item.name.lower(),
        ):
            me = guild.me
            channels: list[dict[str, Any]] = []

            for channel in sorted(
                guild.text_channels,
                key=lambda item: item.position,
            ):
                permissions = channel.permissions_for(me)

                if not permissions.view_channel:
                    continue

                if not permissions.send_messages:
                    continue

                channels.append(
                    {
                        "id": channel.id,
                        "name": channel.name,
                        "category": (
                            channel.category.name
                            if channel.category is not None
                            else None
                        ),
                    }
                )

            guilds.append(
                {
                    "id": guild.id,
                    "name": guild.name,
                    "member_count": guild.member_count,
                    "channels": channels,
                }
            )

        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "started_at": self.started_at.isoformat(),
            "latency_ms": round(self.bot.latency * 1000),
            "bot_user": (
                {
                    "id": self.bot.user.id,
                    "name": str(self.bot.user),
                }
                if self.bot.user is not None
                else None
            ),
            "guilds": guilds,
        }

        write_json_atomic(GUILDS_FILE, payload)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.write_guild_data()
        self.write_public_stats()

        print(
            "[app_bridge] Saved live data for "
            f"{len(self.bot.guilds)} guild(s)."
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.write_guild_data()
        self.write_public_stats()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        store = SetupConfigStore(DB_PATH)

        store.reset_guild(guild.id)

        self.write_guild_data()
        self.write_public_stats()

        print(
            "[app_bridge] Observer was removed from "
            f"{guild.name} ({guild.id}). "
            "Deleted its saved setup configuration."
        )

    @tasks.loop(seconds=10)
    async def refresh_guild_data(self) -> None:
        self.write_guild_data()

    @tasks.loop(minutes=10)
    async def publish_stats(self) -> None:
        self.write_public_stats()

    @publish_stats.before_loop
    async def before_publish_stats(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command_completion(self, context: commands.Context) -> None:
        self.commands_run += 1

    @refresh_guild_data.before_loop
    async def before_refresh_guild_data(self) -> None:
        await self.bot.wait_until_ready()

    def read_requests(self) -> list[dict[str, Any]]:
        if not REQUESTS_FILE.is_file():
            return []

        try:
            loaded = json.loads(
                REQUESTS_FILE.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return []

        return loaded if isinstance(loaded, list) else []

    def write_requests(self, requests: list[dict[str, Any]]) -> None:
        write_json_atomic(REQUESTS_FILE, requests)

    def read_results(self) -> list[dict[str, Any]]:
        if not RESULTS_FILE.is_file():
            return []

        try:
            loaded = json.loads(
                RESULTS_FILE.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return []

        return loaded if isinstance(loaded, list) else []

    def write_result(
        self,
        request_id: str,
        *,
        success: bool,
        message: str,
    ) -> None:
        results = self.read_results()

        results.append(
            {
                "id": request_id,
                "success": success,
                "message": message,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

        write_json_atomic(RESULTS_FILE, results[-100:])

    @tasks.loop(seconds=1)
    async def process_message_requests(self) -> None:
        requests = self.read_requests()

        if not requests:
            return

        self.write_requests([])

        for request in requests:
            request_id = str(request.get("id", "unknown"))
            channel_id = request.get("channel_id")
            content = str(request.get("content", "")).strip()

            if not isinstance(channel_id, int):
                self.write_result(
                    request_id,
                    success=False,
                    message="Invalid channel ID.",
                )
                continue

            if not content:
                self.write_result(
                    request_id,
                    success=False,
                    message="Message cannot be empty.",
                )
                continue

            if len(content) > 2000:
                self.write_result(
                    request_id,
                    success=False,
                    message=(
                        "Discord messages cannot exceed 2,000 characters."
                    ),
                )
                continue

            channel = self.bot.get_channel(channel_id)

            if not isinstance(
                channel,
                (
                    discord.TextChannel,
                    discord.Thread,
                ),
            ):
                self.write_result(
                    request_id,
                    success=False,
                    message=(
                        "That text channel was not found. Observer may no "
                        "longer be able to access it."
                    ),
                )
                continue

            try:
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            except discord.Forbidden:
                self.write_result(
                    request_id,
                    success=False,
                    message=(
                        "Observer lacks permission to view or send messages "
                        "in that channel."
                    ),
                )

            except discord.HTTPException as error:
                self.write_result(
                    request_id,
                    success=False,
                    message=f"Discord rejected the message: {error}",
                )

            else:
                self.write_result(
                    request_id,
                    success=True,
                    message=f"Message sent to #{channel.name}.",
                )

    @process_message_requests.before_loop
    async def before_process_message_requests(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AppBridge(bot))