"""HTTP backend and static host for Observer's Discord Activity."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web

from cogs.config import BOT_OWNER_ID
from cogs.setup_ui import (
    DB_PATH,
    MODULES,
    MAX_TICKET_REASON_OPTIONS,
    TICKET_PANEL_DEFAULTS,
    get_setting_value,
    setting_visible,
)
from database import connect_sqlite

logger = logging.getLogger("observer.activity")
ACTIVITY_DIR = Path(__file__).resolve().parent / "activity"
SESSION_TTL = 7 * 24 * 60 * 60


def _report_user_id(value: str) -> str | None:
    match = re.search(
        r"<@!?(\d{15,22})>|\((\d{15,22})\)|\b(\d{15,22})\b",
        value or "",
    )
    return next((group for group in match.groups() if group), None) if match else None


def _session_storage_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


class ActivityServer:
    def __init__(self, bot):
        self.bot = bot
        self.started = monotonic()
        self.runner: web.AppRunner | None = None
        self.session: aiohttp.ClientSession | None = None
        self.site: web.TCPSite | None = None
        self.client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "https://observer-dashboard.observer").rstrip("/")
        self.sessions: dict[str, dict] = {}
        self.oauth_states: dict[str, float] = {}
        with connect_sqlite(DB_PATH) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS activity_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_json TEXT NOT NULL,
                    guilds_json TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )"""
            )
            db.execute("DELETE FROM activity_sessions WHERE expires_at <= ?", (datetime.now(timezone.utc).timestamp(),))

    async def start(self) -> None:
        if not self.client_id or not self.client_secret:
            logger.info("Activity web server disabled; set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET to enable it")
            return
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/auth/login", self.login)
        app.router.add_get("/auth/callback", self.oauth_callback)
        app.router.add_post("/auth/logout", self.logout)
        app.router.add_get("/api/session", self.session_info)
        app.router.add_get("/api/servers", self.servers)
        app.router.add_get("/api/control-panel/{guild_id}", self.control_panel)
        app.router.add_post("/api/control-panel/{guild_id}/setting", self.save_control_panel_setting)
        app.router.add_get("/api/mod-dashboard/{guild_id}", self.mod_dashboard)
        app.router.add_post("/api/mod-dashboard/{guild_id}/reports/{message_id}/{action}", self.mod_report_action)
        app.router.add_get("/api/public-info", self.public_info)
        app.router.add_post("/api/token", self.exchange_code)
        app.router.add_get("/api/activity/{view}", self.activity_data)
        app.router.add_get("/healthz", self.health)
        app.router.add_get("/observer.png", self.observer_icon)
        app.router.add_get("/observer-icon.svg", self.observer_favicon)
        app.router.add_get("/og-card.png", self.og_card)
        app.router.add_get("/", self.index)
        app.router.add_get("/staff", self.index)
        app.router.add_get("/setup", self.index)
        app.router.add_get("/moderate", self.index)
        assets = ACTIVITY_DIR / "dist" / "assets"
        if assets.is_dir():
            app.router.add_static("/assets/", assets, show_index=False)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        host = os.getenv("ACTIVITY_HOST", "127.0.0.1")
        port = int(os.getenv("ACTIVITY_PORT", "8081"))
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        logger.info("Observer Activity server listening on %s:%s", host, port)

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url}/auth/callback"

    @staticmethod
    def _secure_cookie() -> bool:
        return os.getenv("PUBLIC_BASE_URL", "https://observer-dashboard.observer").startswith("https://")

    async def login(self, request: web.Request) -> web.Response:
        if not self.client_id or not self.client_secret:
            raise web.HTTPServiceUnavailable(text="Discord login is not configured")
        state = secrets.token_urlsafe(32)
        self.oauth_states[state] = monotonic() + 600
        # Expire stale one-time states while keeping the map bounded.
        self.oauth_states = {key: expiry for key, expiry in self.oauth_states.items() if expiry > monotonic()}
        query = urlencode({
            "client_id": self.client_id,
            "redirect_uri": self._oauth_redirect_uri(),
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        })
        response = web.HTTPFound(f"https://discord.com/oauth2/authorize?{query}")
        response.set_cookie("observer_oauth_state", state, max_age=600, httponly=True, secure=self._secure_cookie(), samesite="Lax", path="/")
        return response

    async def oauth_callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state", "")
        expected = request.cookies.get("observer_oauth_state", "")
        expiry = self.oauth_states.pop(state, 0)
        if not state or not secrets.compare_digest(state, expected) or expiry < monotonic():
            raise web.HTTPBadRequest(text="Discord sign-in expired. Please try again.")
        code = request.query.get("code", "")
        if not code:
            raise web.HTTPBadRequest(text="Discord did not return an authorization code")
        assert self.session is not None
        try:
            async with self.session.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._oauth_redirect_uri(),
                },
            ) as response:
                tokens = await response.json()
                if response.status != 200:
                    logger.warning("Discord OAuth code exchange failed with status %s", response.status)
                    raise web.HTTPUnauthorized(text="Discord sign-in could not be completed")
            access_token = tokens.get("access_token")
            if not access_token:
                raise web.HTTPUnauthorized(text="Discord did not authorize this account")
            async with self.session.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                user = await response.json()
                if response.status != 200:
                    raise web.HTTPUnauthorized(text="Could not read your Discord account")
            async with self.session.get(
                "https://discord.com/api/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as response:
                guilds = await response.json()
                if response.status != 200 or not isinstance(guilds, list):
                    raise web.HTTPBadGateway(text="Could not load your Discord servers")
            key = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc).timestamp() + SESSION_TTL
            manageable = [guild for guild in guilds if self._oauth_can_moderate(guild)]
            session = {"user": user, "guilds": manageable, "expires_at": expires_at}
            self.sessions[key] = session
            with connect_sqlite(DB_PATH) as db:
                db.execute(
                    "INSERT INTO activity_sessions (session_id, user_json, guilds_json, expires_at) VALUES (?, ?, ?, ?)",
                    (_session_storage_key(key), json.dumps(user), json.dumps(session["guilds"]), expires_at),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("Discord OAuth callback failed")
            raise web.HTTPBadGateway(text="Discord sign-in is temporarily unavailable")
        response = web.HTTPFound("/?dashboard=1")
        response.del_cookie("observer_oauth_state", path="/")
        response.set_cookie("observer_session", key, max_age=SESSION_TTL, httponly=True, secure=self._secure_cookie(), samesite="Lax", path="/")
        return response

    async def public_info(self, request: web.Request) -> web.Response:
        support_guild_id = os.getenv("OBSERVER_SUPPORT_GUILD_ID", "1535974879554437210")
        info = {
            "creator": os.getenv("OBSERVER_CREATOR_NAME", "").strip() or "Rocky / Rockus26",
            "support_url": os.getenv("OBSERVER_SUPPORT_URL", f"https://discord.com/channels/{support_guild_id}"),
            "source_url": os.getenv("OBSERVER_SOURCE_URL", "").strip() or "https://github.com/blurock2/Observer-cogs",
            "portfolio_url": os.getenv("OBSERVER_PORTFOLIO_URL", "").strip() or "https://my-website.rockus26.workers.dev/#projects",
            "kofi_url": os.getenv("OBSERVER_KOFI_URL", "").strip() or "https://ko-fi.com/supporting_observer",
        }
        return web.json_response(info, headers={"Cache-Control": "public, max-age=300"})

    def _get_session(self, request: web.Request) -> dict:
        key = request.cookies.get("observer_session", "")
        session = self._load_session(key)
        if session is None:
            raise web.HTTPUnauthorized(text="Sign in with Discord to continue")
        return session

    def _load_session(self, key: str) -> dict | None:
        if not key:
            return None
        session = self.sessions.get(key)
        if session is None:
            with connect_sqlite(DB_PATH) as db:
                row = db.execute(
                    "SELECT user_json, guilds_json, expires_at FROM activity_sessions WHERE session_id = ?",
                    (_session_storage_key(key),),
                ).fetchone()
            if row is None:
                return None
            try:
                session = {
                    "user": json.loads(row["user_json"]),
                    "guilds": json.loads(row["guilds_json"]),
                    "expires_at": float(row["expires_at"]),
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                session = None
        if session is None or session.get("expires_at", 0) <= datetime.now(timezone.utc).timestamp():
            self.sessions.pop(key, None)
            with connect_sqlite(DB_PATH) as db:
                db.execute("DELETE FROM activity_sessions WHERE session_id = ?", (_session_storage_key(key),))
            return None
        self.sessions[key] = session
        return session

    async def session_info(self, request: web.Request) -> web.Response:
        key = request.cookies.get("observer_session", "")
        session = self._load_session(key)
        if session is None:
            return web.json_response({"signed_in": False}, headers={"Cache-Control": "no-store"})
        user = session["user"]
        return web.json_response({
            "signed_in": True,
            "user": {"id": user.get("id"), "username": user.get("global_name") or user.get("username"), "avatar": user.get("avatar")},
        }, headers={"Cache-Control": "no-store"})

    async def _user_guilds(self, session: dict) -> list[dict]:
        if "guilds" in session:
            return session["guilds"]
        assert self.session is not None
        if monotonic() > session["expires_at"] - 30:
            refresh_token = session.get("refresh_token")
            if not refresh_token:
                raise web.HTTPUnauthorized(text="Your Discord session expired. Sign in again.")
            try:
                async with self.session.post(
                    "https://discord.com/api/oauth2/token",
                    data={"client_id": self.client_id, "client_secret": self.client_secret,
                          "grant_type": "refresh_token", "refresh_token": refresh_token},
                ) as response:
                    tokens = await response.json()
                    if response.status != 200:
                        raise web.HTTPUnauthorized(text="Your Discord session expired. Sign in again.")
                session["access_token"] = tokens["access_token"]
                session["refresh_token"] = tokens.get("refresh_token", refresh_token)
                session["expires_at"] = monotonic() + int(tokens.get("expires_in", 3600))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.exception("Discord OAuth token refresh failed")
                raise web.HTTPBadGateway(text="Discord is temporarily unavailable")
        try:
            async with self.session.get(
                "https://discord.com/api/users/@me/guilds",
                headers={"Authorization": f"Bearer {session['access_token']}"},
            ) as response:
                data = await response.json()
                if response.status == 401:
                    raise web.HTTPUnauthorized(text="Your Discord session expired. Sign in again.")
                if response.status != 200:
                    raise web.HTTPBadGateway(text="Could not load your Discord servers")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("Could not load Discord server list")
            raise web.HTTPBadGateway(text="Discord is temporarily unavailable")

    async def servers(self, request: web.Request) -> web.Response:
        session = self._get_session(request)
        try:
            await asyncio.wait_for(self.bot.wait_until_ready(), timeout=15)
        except asyncio.TimeoutError:
            raise web.HTTPServiceUnavailable(text="Observer is connecting to Discord. Try again shortly.")
        manageable = [guild for guild in await self._user_guilds(session) if self._oauth_can_moderate(guild)]
        items = []
        for guild in manageable:
            try:
                guild_id = int(guild["id"])
            except (KeyError, TypeError, ValueError):
                continue
            bot_guild = self.bot.get_guild(guild_id)
            items.append({
                "id": str(guild_id),
                "name": guild.get("name", "Discord server"),
                "icon": guild.get("icon"),
                "bot_present": bot_guild is not None,
                "is_owner": guild.get("owner") is True,
            })
        items.sort(key=lambda item: (not item["bot_present"], item["name"].casefold()))
        return web.json_response({"servers": items, "invite_url": self._bot_invite_url()}, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _oauth_can_moderate(guild: dict) -> bool:
        if guild.get("owner") is True:
            return True
        try:
            permissions = int(guild.get("permissions", "0"))
        except (TypeError, ValueError):
            return False
        # Administrator, Manage Server, Manage Messages, Moderate Members,
        # Kick Members, or Ban Members are sufficient for report review.
        return bool(permissions & ((1 << 3) | (1 << 5) | (1 << 13) | (1 << 40) | (1 << 1) | (1 << 2)))

    async def _moderator_guild(self, request: web.Request) -> tuple[dict, discord.Guild]:
        session = self._get_session(request)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="A valid server ID is required")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="Observer is not in that server")
        user_id = str(session["user"].get("id", ""))
        if str(guild.owner_id) == user_id:
            return session, guild
        oauth_guild = next((item for item in session.get("guilds", []) if str(item.get("id")) == str(guild_id)), None)
        if oauth_guild and self._oauth_can_moderate(oauth_guild):
            return session, guild
        try:
            member = await guild.fetch_member(int(user_id))
        except (discord.HTTPException, TypeError, ValueError):
            raise web.HTTPForbidden(text="You need server moderation permissions to view reports")
        perms = member.guild_permissions
        if any((perms.administrator, perms.manage_guild, perms.manage_messages, perms.moderate_members, perms.kick_members, perms.ban_members)):
            return session, guild
        role_ids = {role.id for role in member.roles}
        for module, key in (("moderation", "mod_role"), ("moderation", "head_mod_role"), ("bot", "staff_role"), ("report_msg", "staff_role")):
            try:
                role_id = int(self.bot.setup_store.get(guild_id, module, key))
            except (TypeError, ValueError):
                continue
            if role_id in role_ids:
                return session, guild
        raise web.HTTPForbidden(text="You need server moderation permissions to view reports")

    async def mod_dashboard(self, request: web.Request) -> web.Response:
        session, guild = await self._moderator_guild(request)
        raw_channel_id = self.bot.setup_store.get(guild.id, "report_msg", "report_channel")
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            channel_id = 0
        channel = guild.get_channel(channel_id) if channel_id else None
        reports = []
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                async for message in channel.history(limit=100):
                    if not message.embeds or message.embeds[0].title != "New Message Report":
                        continue
                    embed = message.embeds[0]
                    fields = {field.name.casefold(): field.value for field in embed.fields}
                    target_raw = fields.get("message author", "")
                    target_match = re.search(r"`(\d{15,22})`", target_raw)
                    target_id = target_match.group(1) if target_match else ""
                    member = guild.get_member(int(target_id)) if target_id.isdigit() else None
                    # Claiming is a work assignment, not a resolution. Reports
                    # stay open until staff accepts/closes them.
                    closed = "accepted by staff" in fields
                    claimed_fields = [field for field in embed.fields if field.name.casefold() == "claimed by"]
                    claimed_id = next(
                        (user_id for field in reversed(claimed_fields) if (user_id := _report_user_id(field.value))),
                        None,
                    )
                    claimed_member = guild.get_member(int(claimed_id)) if claimed_id else None
                    accepted_raw = fields.get("accepted by staff", "")
                    accepted_match = re.search(r"<@!?(\d{15,22})>", accepted_raw)
                    accepted_id = accepted_match.group(1) if accepted_match else None
                    accepted_member = guild.get_member(int(accepted_id)) if accepted_id else None
                    escalated_raw = fields.get("escalated by", "")
                    escalated_match = re.search(r"\((\d{15,22})\)", escalated_raw)
                    escalated_id = escalated_match.group(1) if escalated_match else None
                    escalated_member = guild.get_member(int(escalated_id)) if escalated_id else None
                    message_link = fields.get("message link", "")
                    linked_message_match = re.search(
                        r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/\d+/\d+/(\d{15,22})",
                        message_link,
                    )
                    reports.append({
                        "id": str(message.id),
                        "target_name": member.display_name if member else (f"User {target_id}" if target_id else "Unknown member"),
                        "target_id": target_id,
                        "channel": fields.get("channel", "").split(" ", 1)[0],
                        "reason": fields.get("reason", "No reason provided"),
                        "created_at": message.created_at.isoformat(),
                        "url": message.jump_url,
                        "status": "closed" if closed else "open",
                        "claimed": bool(claimed_fields),
                        "claimed_by": claimed_id,
                        "claimed_by_name": claimed_member.display_name if claimed_member else (f"User {claimed_id}" if claimed_id else None),
                        "accepted": "accepted by staff" in fields,
                        "accepted_by_name": accepted_member.display_name if accepted_member else (f"User {accepted_id}" if accepted_id else None),
                        "escalated_by_name": escalated_member.display_name if escalated_member else (f"User {escalated_id}" if escalated_id else None),
                        "reported_message_id": linked_message_match.group(1) if linked_message_match else None,
                        "linked_actions": [],
                    })
            except discord.Forbidden:
                raise web.HTTPServiceUnavailable(text="Observer needs permission to view the configured reports channel")
            except discord.HTTPException:
                raise web.HTTPBadGateway(text="Could not load reports from Discord")
        source_ids = sorted({report["reported_message_id"] for report in reports if report["reported_message_id"]})
        if source_ids:
            with connect_sqlite(DB_PATH) as db:
                case_table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='moderation_cases'"
                ).fetchone()
                if case_table:
                    placeholders = ",".join("?" for _ in source_ids)
                    rows = db.execute(
                        "SELECT case_number, case_type, target_id, moderator_id, reason, status, created_at, source_message_id "
                        f"FROM moderation_cases WHERE guild_id=? AND source_message_id IN ({placeholders}) "
                        "ORDER BY id DESC",
                        (guild.id, *[int(source_id) for source_id in source_ids]),
                    ).fetchall()
                    linked_by_message = {}
                    for row in rows:
                        target = guild.get_member(int(row["target_id"]))
                        moderator = guild.get_member(int(row["moderator_id"])) if row["moderator_id"] else None
                        linked_by_message.setdefault(str(row["source_message_id"]), []).append({
                            "case_number": row["case_number"],
                            "case_type": row["case_type"],
                            "target_name": target.display_name if target else str(row["target_id"]),
                            "moderator_name": moderator.display_name if moderator else (str(row["moderator_id"]) if row["moderator_id"] else "Observer"),
                            "reason": row["reason"] or "No reason recorded",
                            "status": row["status"],
                            "created_at": row["created_at"],
                        })
                    for report in reports:
                        report["linked_actions"] = linked_by_message.get(report["reported_message_id"], [])

        open_reports = sum(report["status"] == "open" for report in reports)
        recent_actions = []
        with connect_sqlite(DB_PATH) as db:
            case_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='moderation_cases'"
            ).fetchone()
            if case_table:
                rows = db.execute(
                    "SELECT case_number, case_type, target_id, moderator_id, reason, status, created_at "
                    "FROM moderation_cases WHERE guild_id=? AND case_type!='report' "
                    "ORDER BY id DESC LIMIT 6",
                    (guild.id,),
                ).fetchall()
                for row in rows:
                    target = guild.get_member(int(row["target_id"]))
                    moderator = guild.get_member(int(row["moderator_id"])) if row["moderator_id"] else None
                    recent_actions.append({
                        "case_number": row["case_number"],
                        "case_type": row["case_type"],
                        "target_id": str(row["target_id"]),
                        "target_name": target.display_name if target else str(row["target_id"]),
                        "moderator_name": moderator.display_name if moderator else (str(row["moderator_id"]) if row["moderator_id"] else "Observer"),
                        "reason": row["reason"] or "No reason recorded",
                        "status": row["status"],
                        "created_at": row["created_at"],
                    })
        return web.json_response({
            "guild": {"id": str(guild.id), "name": guild.name, "icon": guild.icon.key if guild.icon else None,
                      "is_owner": str(guild.owner_id) == str(session["user"].get("id", ""))},
            "viewer_id": str(session["user"].get("id", "")),
            "bot_online": self.bot.is_ready(),
            "member_count": guild.member_count or 0,
            "open_reports": open_reports,
            "reports": reports,
            "recent_actions": recent_actions,
            "reports_configured": bool(channel_id),
            "reports_channel": channel.name if channel else None,
        }, headers={"Cache-Control": "no-store"})

    async def _web_moderation_action(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        action: str,
        target_id: int,
        source_channel_id: int | None,
        source_message_id: int | None,
        reason: str,
        duration_minutes: int | None = None,
    ) -> dict:
        moderation = self.bot.get_cog("ModerationCog")
        if moderation is None:
            raise web.HTTPServiceUnavailable(text="Observer's moderation tools are unavailable")
        from cogs.config import is_bot_owner

        configured_roles = {
            self.bot.setup_store.get(guild.id, "moderation", "mod_role"),
            self.bot.setup_store.get(guild.id, "moderation", "head_mod_role"),
        }
        role_ids = {role.id for role in actor.roles}
        has_mod_role = any(str(role_id).isdigit() and int(role_id) in role_ids for role_id in configured_roles)
        if actor.id != guild.owner_id and not is_bot_owner(actor) and not has_mod_role:
            raise web.HTTPForbidden(text="This action requires the configured moderator or head moderator role")

        target = guild.get_member(target_id)
        if action in {"warn", "timeout"}:
            if target is None:
                try:
                    target = await guild.fetch_member(target_id)
                except discord.NotFound:
                    raise web.HTTPNotFound(text="The reported member is no longer in this server")
                except discord.HTTPException:
                    raise web.HTTPBadGateway(text="Could not load the reported member")
            if target.id == actor.id or target.id == guild.owner_id:
                raise web.HTTPForbidden(text="You cannot moderate yourself or the server owner")
            if actor.id != guild.owner_id and target.top_role >= actor.top_role:
                raise web.HTTPForbidden(text="You cannot moderate a member with equal or higher roles")
            bot_member = guild.me
            if bot_member is not None and target.top_role >= bot_member.top_role:
                raise web.HTTPForbidden(text="Observer cannot act on a member with an equal or higher role")
        elif action == "delete":
            if not source_channel_id or not source_message_id:
                raise web.HTTPConflict(text="The original reported message link is missing")
            source_channel = guild.get_channel(source_channel_id) or guild.get_thread(source_channel_id)
            if source_channel is None:
                try:
                    source_channel = await guild.fetch_channel(source_channel_id)
                except discord.NotFound:
                    raise web.HTTPNotFound(text="The original message channel no longer exists")
            if not isinstance(source_channel, (discord.TextChannel, discord.Thread)):
                raise web.HTTPConflict(text="The original report is not in a deletable text channel")
            bot_member = guild.me
            if bot_member is None or not source_channel.permissions_for(bot_member).manage_messages:
                raise web.HTTPForbidden(text="Observer needs Manage Messages in the original message channel")
            if target is None:
                try:
                    target = await self.bot.fetch_user(target_id)
                except discord.HTTPException:
                    raise web.HTTPBadGateway(text="Observer could not load the reported member profile")
            try:
                source_message = await source_channel.fetch_message(source_message_id)
            except discord.NotFound:
                raise web.HTTPConflict(text="The original reported message has already been deleted")
            try:
                await source_message.delete(reason=f"Deleted after report review by {actor}: {reason}")
            except discord.Forbidden:
                raise web.HTTPForbidden(text="Observer cannot delete the original message")
            except discord.HTTPException:
                raise web.HTTPBadGateway(text="Discord rejected deletion of the original message")
        else:
            raise web.HTTPBadRequest(text="Unsupported moderation action")

        if action == "timeout":
            if duration_minutes is None or not 1 <= duration_minutes <= 40320:
                raise web.HTTPBadRequest(text="Timeout duration must be between 1 and 40,320 minutes")
            expires = discord.utils.utcnow() + timedelta(minutes=duration_minutes)
            try:
                await target.timeout(expires, reason=reason)
            except discord.Forbidden:
                raise web.HTTPForbidden(text="Observer cannot timeout this member")
            except discord.HTTPException:
                raise web.HTTPBadGateway(text="Discord rejected the timeout")
            from moderation_notifications import dm_moderation_action
            await dm_moderation_action(target, guild, f"Timeout for {duration_minutes} minute(s)", reason)
            action_id = moderation.actions.create(guild.id, "timeout", target.id, actor.id)
            case_id = moderation.cases.create_case(
                guild.id, "timeout", target.id, actor.id, reason,
                status="active", action_id=action_id, expires_at=expires.isoformat(),
                source_message_id=source_message_id,
            )
            moderation.mod_stats.increment(guild.id, actor.id, "timeouts")
            await moderation._send_mod_log(guild, "Timeout", actor, target, reason, action_id=action_id, case_id=case_id)
        elif action == "warn":
            from moderation_notifications import dm_moderation_action
            await dm_moderation_action(target, guild, "Warning", reason)
            action_id = moderation.actions.create(guild.id, "warn", target.id, actor.id, status="resolved")
            case_id = moderation.cases.create_case(
                guild.id, "warn", target.id, actor.id, reason,
                status="resolved", action_id=action_id, source_message_id=source_message_id,
            )
            await moderation._send_mod_log(guild, "Warning", actor, target, reason, action_id=action_id, case_id=case_id, undoable=False)
        else:
            action_id = moderation.actions.create(guild.id, "delete", target.id, actor.id, status="resolved")
            case_id = moderation.cases.create_case(
                guild.id, "delete", target.id, actor.id, reason,
                status="resolved", action_id=action_id, source_message_id=source_message_id,
            )
            await moderation._send_mod_log(guild, "Delete reported message", actor, target, reason, action_id=action_id, case_id=case_id, undoable=False)
        case = moderation.cases.get_case(case_id)
        return {"case_number": int(case["case_number"]), "case_id": int(case_id), "action": action}

    async def mod_report_action(self, request: web.Request) -> web.Response:
        session, guild = await self._moderator_guild(request)
        action = request.match_info.get("action", "")
        if action not in {"claim", "unclaim", "accept", "close", "warn", "timeout", "delete", "escalate"}:
            raise web.HTTPNotFound(text="Unknown report action")
        try:
            message_id = int(request.match_info["message_id"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="A valid report ID is required")

        user_id = int(session["user"].get("id", 0))
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except (discord.HTTPException, ValueError):
            raise web.HTTPForbidden(text="Could not verify your server permissions")
        staff_role_id = self.bot.setup_store.get(guild.id, "report_msg", "staff_role")
        try:
            staff_role_id = int(staff_role_id) if staff_role_id is not None else None
        except (TypeError, ValueError):
            staff_role_id = None
        is_report_staff = user_id == guild.owner_id or (
            member is not None and (
                member.guild_permissions.manage_messages
                or (staff_role_id is not None and any(role.id == staff_role_id for role in member.roles))
            )
        )
        if action in {"claim", "unclaim", "accept", "close", "escalate"} and not is_report_staff:
            raise web.HTTPForbidden(text="You need Manage Messages or the configured report staff role to act on reports")
        payload = {}
        if action in {"warn", "timeout", "delete"}:
            try:
                payload = await request.json()
            except (ValueError, web.HTTPException):
                raise web.HTTPBadRequest(text="Provide a reason and confirm the moderation action")
            if not isinstance(payload, dict):
                raise web.HTTPBadRequest(text="Invalid moderation action")
            if action == "delete" and payload.get("confirm") is not True:
                raise web.HTTPBadRequest(text="Confirm deletion of the original reported message")
            raw_reason = payload.get("reason", "")
            if not isinstance(raw_reason, str) or len(raw_reason) > 1000:
                raise web.HTTPBadRequest(text="Reason must be 1,000 characters or fewer")
            payload["reason"] = raw_reason.strip()
            if action == "timeout":
                duration = payload.get("duration_minutes")
                if type(duration) is not int or not 1 <= duration <= 40320:
                    raise web.HTTPBadRequest(text="Timeout duration must be between 1 and 40,320 minutes")
        elif action == "escalate" and not isinstance(member, discord.Member):
            raise web.HTTPForbidden(text="Could not verify your moderator account")

        report_cog = self.bot.get_cog("ReportMessage")
        if report_cog is None:
            raise web.HTTPServiceUnavailable(text="Observer's report controls are unavailable")
        try:
            report_channel_id = int(report_cog.get_report_channel_id(guild.id) or 0)
        except (TypeError, ValueError):
            report_channel_id = 0
        channel = guild.get_channel(report_channel_id) if report_channel_id else None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise web.HTTPConflict(text="This server has no valid reports channel configured")
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            raise web.HTTPNotFound(text="This report could not be found in the configured reports channel")
        except discord.Forbidden:
            raise web.HTTPServiceUnavailable(text="Observer cannot access the reports channel")
        except discord.HTTPException:
            raise web.HTTPBadGateway(text="Could not load this report from Discord")
        if not message.embeds or message.embeds[0].title != "New Message Report":
            raise web.HTTPBadRequest(text="That message is not an Observer report")

        embed = message.embeds[0]
        claimed_fields = [field for field in embed.fields if field.name.casefold() == "claimed by"]
        accepted = any(field.name.casefold() == "accepted by staff" for field in embed.fields)
        claimed_id = next(
            (user_id for field in reversed(claimed_fields) if (user_id := _report_user_id(field.value))),
            None,
        )
        claimed_by = int(claimed_id) if claimed_id else None
        if action not in {"claim", "unclaim"} and claimed_by != user_id:
            if claimed_by is None:
                raise web.HTTPForbidden(text="Claim this report before taking action")
            raise web.HTTPForbidden(text="Only the moderator who claimed this report can take action")
        fields = {field.name.casefold(): field.value for field in embed.fields}
        target_match = re.search(r"\((\d{15,22})\)", fields.get("message author", ""))
        if not target_match:
            raise web.HTTPConflict(text="The report does not contain a valid reported member ID")
        target_id = int(target_match.group(1))
        link_match = re.search(r"discord(?:app)?\.com/channels/\d+/(\d+)/(\d{15,22})", fields.get("message link", ""))
        source_channel_id = int(link_match.group(1)) if link_match else None
        source_message_id = int(link_match.group(2)) if link_match else None
        action_result = None
        escalated = any(field.name.casefold() == "escalated by" for field in embed.fields)
        reporter_id = None
        newly_accepted = False
        reopened_accepted = False
        report_cog_member = guild.get_member(user_id)
        display_name = report_cog_member.display_name if report_cog_member else session["user"].get("global_name") or session["user"].get("username", "Moderator")

        if action == "claim":
            if claimed_fields:
                raise web.HTTPConflict(text="This report has already been claimed")
            embed.add_field(name="Claimed by", value=f"<@{user_id}> (`{user_id}`)", inline=False)
            claimed_by = user_id
            report_cog.mod_stats.increment(guild.id, user_id, "reports_claimed")
        elif action == "unclaim":
            if not claimed_fields:
                raise web.HTTPConflict(text="This report is not currently claimed")
            if claimed_by != user_id and user_id != guild.owner_id:
                try:
                    moderator = guild.get_member(user_id) or await guild.fetch_member(user_id)
                except discord.HTTPException:
                    raise web.HTTPForbidden(text="Could not verify your server permissions")
                staff_role = report_cog.get_staff_role_id(guild.id)
                is_configured_staff = staff_role is not None and any(role.id == staff_role for role in moderator.roles)
                if not moderator.guild_permissions.manage_messages and not is_configured_staff:
                    raise web.HTTPForbidden(text="Only the claimant or a moderator can unclaim this report")
            if accepted:
                reopened_accepted = True
                accepted = False
            for index in reversed(range(len(embed.fields))):
                field_name = embed.fields[index].name.casefold()
                if field_name == "claimed by" or (reopened_accepted and field_name == "accepted by staff"):
                    embed.remove_field(index)
            claimed_by = None
        elif action in {"accept", "close"}:
            if accepted:
                raise web.HTTPConflict(text="This report has already been accepted")
            reporter_id, newly_accepted = report_cog.reports.accept(message.id, guild.id)
            embed.add_field(
                name="Accepted by staff",
                value=f"<@{user_id}> · <t:{int(datetime.now(timezone.utc).timestamp())}:f>",
                inline=False,
            )
            if reporter_id is not None and newly_accepted:
                month = datetime.now(timezone.utc).strftime("%Y-%m")
                try:
                    await report_cog.update_monthly_reporter_role(guild, month)
                except discord.HTTPException:
                    logger.warning("Report %s was accepted, but its monthly role update failed", message.id, exc_info=True)
        elif action == "escalate":
            if escalated:
                raise web.HTTPConflict(text="This report has already been escalated")
            head_role_id = self.bot.setup_store.get(guild.id, "moderation", "head_mod_role")
            try:
                head_role = guild.get_role(int(head_role_id)) if head_role_id is not None else None
            except (TypeError, ValueError):
                head_role = None
            if head_role is None or head_role.is_default():
                raise web.HTTPConflict(text="Configure a head moderator role before escalating reports")
            await channel.send(
                content=f"{head_role.mention} Report escalated by {member.mention}: {message.jump_url}",
                allowed_mentions=discord.AllowedMentions(roles=[head_role], users=False, everyone=False),
            )
            embed.add_field(name="Escalated by", value=f"<@{user_id}> (`{user_id}`)", inline=False)
        elif action in {"warn", "timeout", "delete"}:
            actor = member
            if not isinstance(actor, discord.Member):
                raise web.HTTPForbidden(text="Could not verify your moderator account")
            reason = payload["reason"] or fields.get("reason", "No reason provided")
            action_result = await self._web_moderation_action(
                guild=guild,
                actor=actor,
                action=action,
                target_id=target_id,
                source_channel_id=source_channel_id,
                source_message_id=source_message_id,
                reason=reason,
                duration_minutes=payload.get("duration_minutes"),
            )
            target = guild.get_member(target_id)
            target_label = target.mention if target else f"User {target_id}"
            embed.add_field(
                name="Moderation action",
                value=f"{action.title()} on {target_label} · Case #{action_result['case_number']} by <@{user_id}>",
                inline=False,
            )
        from cogs.report_msg import ClaimReportView
        view = ClaimReportView(
            report_cog,
            report_cog.get_staff_role_id(guild.id),
            claimed_by=claimed_by,
            accepted=accepted or action in {"accept", "close"},
        )
        if action == "claim":
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == "report_claim":
                    item.label = f"Claimed by {display_name}"[:80]
        try:
            await message.edit(embed=embed, view=view)
            self.bot.add_view(view, message_id=message.id)
        except discord.Forbidden:
            raise web.HTTPServiceUnavailable(text="Observer cannot update report messages in the configured channel")
        except discord.HTTPException:
            raise web.HTTPBadGateway(text="Discord could not update this report")
        if reopened_accepted:
            report_cog.reports.unaccept(message.id, guild.id)
            try:
                await report_cog.update_monthly_reporter_role(
                    guild, datetime.now(timezone.utc).strftime("%Y-%m")
                )
            except discord.HTTPException:
                logger.warning(
                    "Report %s was reopened, but its monthly role update failed",
                    message.id,
                    exc_info=True,
                )
        return web.json_response({"ok": True, "action": action, "accepted": accepted or action in {"accept", "close"}, "claimed_by": str(claimed_by) if claimed_by else None, "case": action_result})

    def _dashboard_guild(self, request: web.Request) -> tuple[dict, discord.Guild]:
        session = self._get_session(request)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPBadRequest(text="A valid server ID is required")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="Observer is not in that server")
        if str(guild.owner_id) != str(session["user"].get("id", "")):
            raise web.HTTPForbidden(text="Only the server owner can configure Observer from this page")
        return session, guild

    def _migrate_legacy_autoroles(self, guild_id: int) -> None:
        """Move the older autorole tables into the shared setup settings."""
        store = self.bot.setup_store
        store.refresh_module(guild_id, "autorole")
        with connect_sqlite(DB_PATH) as db:
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            config = db.execute(
                "SELECT enabled, bot_enabled FROM guild_autorole_config WHERE guild_id = ?",
                (guild_id,),
            ).fetchone() if "guild_autorole_config" in tables else None
            legacy_roles = db.execute(
                "SELECT rowid, role_id, is_bot FROM guild_autoroles WHERE guild_id = ? ORDER BY rowid",
                (guild_id,),
            ).fetchall() if "guild_autoroles" in tables else []

        if config is not None:
            for key in ("enabled", "bot_enabled"):
                if store.get(guild_id, "autorole", key, None) is None:
                    store.set(guild_id, "autorole", key, bool(config[key]))

        migrated: list[int] = []
        for is_bot, prefix in ((0, "member_role_"), (1, "bot_role_")):
            rows = [row for row in legacy_roles if int(row["is_bot"]) == is_bot]
            configured: set[int] = set()
            for index in range(1, 6):
                raw = store.get(guild_id, "autorole", f"{prefix}{index}", None)
                try:
                    configured.add(int(raw))
                except (TypeError, ValueError):
                    pass
            for row in rows:
                role_id = int(row["role_id"])
                if role_id in configured:
                    migrated.append(int(row["rowid"]))
                    continue
                slot = next((index for index in range(1, 6)
                             if store.get(guild_id, "autorole", f"{prefix}{index}", None) is None), None)
                if slot is None:
                    continue
                store.set(guild_id, "autorole", f"{prefix}{slot}", role_id)
                configured.add(role_id)
                migrated.append(int(row["rowid"]))

        if migrated and "guild_autoroles" in tables:
            with connect_sqlite(DB_PATH) as db:
                db.executemany("DELETE FROM guild_autoroles WHERE rowid = ?", [(row_id,) for row_id in migrated])

    async def control_panel(self, request: web.Request) -> web.Response:
        _, guild = self._dashboard_guild(request)
        self._migrate_legacy_autoroles(guild.id)
        try:
            channels = await guild.fetch_channels()
        except discord.HTTPException:
            logger.info("Could not refresh channels for dashboard guild %s; using cache", guild.id)
            channels = list(guild.channels)
        try:
            roles = await guild.fetch_roles()
        except discord.HTTPException:
            logger.info("Could not refresh roles for dashboard guild %s; using cache", guild.id)
            roles = list(guild.roles)

        modules = []
        for module in MODULES:
            self.bot.setup_store.refresh_module(guild.id, module.key)
            settings = []
            for spec in module.settings:
                if not setting_visible(guild.id, module.key, spec.key):
                    continue
                if spec.kind == "ticket_panel":
                    value = {
                        key: self.bot.setup_store.get(
                            guild.id, module.key, key, default=default
                        )
                        for key, default in TICKET_PANEL_DEFAULTS.items()
                    }
                else:
                    value = get_setting_value(
                        self.bot.setup_store, guild, module, spec
                    )
                    # Discord snowflakes exceed JavaScript's exact integer range.
                    # Keep selector values as strings so the browser can match IDs
                    # against the channel and role lists without rounding them.
                    if spec.kind in ("channel", "role") and value is not None:
                        value = str(value)
                settings.append({
                    "key": spec.key,
                    "label": spec.label,
                    "kind": spec.kind,
                    "description": spec.description,
                    "default": spec.default,
                    "value": value,
                })
            modules.append({
                "key": module.key,
                "label": module.label,
                "emoji": module.emoji,
                "description": module.description,
                "settings": settings,
            })

        channel_items = [{
            "id": str(channel.id),
            "name": channel.name,
            "type": getattr(channel.type, "name", str(channel.type)),
            "category_id": str(getattr(channel, "category_id", None)) if getattr(channel, "category_id", None) else None,
        } for channel in sorted(channels, key=lambda item: (item.position, item.name.casefold()))]
        role_items = [{
            "id": str(role.id),
            "name": role.name,
            "position": role.position,
            "is_default": role.is_default(),
        } for role in sorted(roles, key=lambda item: (item.position, item.name.casefold()), reverse=True) if not role.is_default()]
        return web.json_response({
            "guild": {"id": str(guild.id), "name": guild.name, "icon": guild.icon.key if guild.icon else None},
            "modules": modules,
            "channels": channel_items,
            "roles": role_items,
        }, headers={"Cache-Control": "no-store"})

    async def save_control_panel_setting(self, request: web.Request) -> web.Response:
        _, guild = self._dashboard_guild(request)
        try:
            payload = await request.json()
        except (ValueError, web.HTTPException):
            raise web.HTTPBadRequest(text="Invalid request body")
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Invalid setting")
        module = next((item for item in MODULES if item.key == payload.get("module")), None)
        if module is None:
            raise web.HTTPBadRequest(text="Unknown feature")
        key = payload.get("key")
        spec = next((item for item in module.settings if item.key == key), None)
        if spec is None or not setting_visible(guild.id, module.key, spec.key):
            raise web.HTTPBadRequest(text="Unknown setting")
        value = payload.get("value")
        store = self.bot.setup_store

        if spec.kind == "ticket_panel":
            if not isinstance(value, dict):
                raise web.HTTPBadRequest(text="Invalid ticket panel settings")
            title = value.get("panel_title")
            description = value.get("panel_description")
            footer = value.get("panel_footer")
            reasons = value.get("reason_options")
            if not isinstance(title, str) or not title.strip() or len(title) > 256:
                raise web.HTTPBadRequest(text="Panel title must be 1–256 characters")
            if not isinstance(description, str) or len(description) > 2048:
                raise web.HTTPBadRequest(text="Panel description must be at most 2048 characters")
            if not isinstance(footer, str) or len(footer) > 2048:
                raise web.HTTPBadRequest(text="Panel footer must be at most 2048 characters")
            if (not isinstance(reasons, list) or not 1 <= len(reasons) <= MAX_TICKET_REASON_OPTIONS
                    or any(not isinstance(reason, str) or not reason.strip() or len(reason) > 100 for reason in reasons)):
                raise web.HTTPBadRequest(text=f"Add 1–{MAX_TICKET_REASON_OPTIONS} ticket reasons of up to 100 characters each")
            for field, field_value in (("panel_title", title.strip()), ("panel_description", description), ("panel_footer", footer), ("reason_options", [reason.strip() for reason in reasons])):
                store.set(guild.id, module.key, field, field_value)
            return web.json_response({"ok": True, "value": value}, headers={"Cache-Control": "no-store"})

        if spec.kind == "toggle":
            if not isinstance(value, bool):
                raise web.HTTPBadRequest(text="Toggle value must be true or false")
        elif spec.kind == "integer":
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise web.HTTPBadRequest(text="Enter a whole number between 0 and 1,000,000")
        elif spec.kind == "text":
            if not isinstance(value, str) or len(value) > 4000:
                raise web.HTTPBadRequest(text="Text values must be 4,000 characters or fewer")
        elif spec.kind == "channel":
            if value in (None, ""):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise web.HTTPBadRequest(text="Choose a valid channel")
                if guild.get_channel(value) is None:
                    raise web.HTTPBadRequest(text="That channel is not in this server")
        elif spec.kind == "role":
            if value in (None, ""):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise web.HTTPBadRequest(text="Choose a valid role")
                if guild.get_role(value) is None or value == guild.default_role.id:
                    raise web.HTTPBadRequest(text="That role cannot be selected")
        else:
            raise web.HTTPBadRequest(text="This setting is not available in the web panel")

        store.set(guild.id, module.key, spec.key, value)
        return web.json_response({"ok": True, "value": value}, headers={"Cache-Control": "no-store"})

    def _bot_invite_url(self) -> str:
        if not self.client_id:
            return "https://discord.com/developers/applications"
        # Request feature permissions without granting Administrator. Server
        # owners can review and narrow this set on Discord's authorization screen.
        permissions = "1496694516983"
        query = urlencode({"client_id": self.client_id, "permissions": permissions, "scope": "bot applications.commands"})
        return f"https://discord.com/oauth2/authorize?{query}"

    async def logout(self, request: web.Request) -> web.Response:
        key = request.cookies.get("observer_session", "")
        self.sessions.pop(key, None)
        with connect_sqlite(DB_PATH) as db:
            db.execute("DELETE FROM activity_sessions WHERE session_id = ?", (_session_storage_key(key),))
        response = web.json_response({"ok": True})
        response.del_cookie("observer_session", path="/")
        return response

    async def index(self, request: web.Request) -> web.StreamResponse:
        page = ACTIVITY_DIR / "dist" / "index.html"
        if not page.is_file():
            return web.json_response({"error": "Observer's website is not built yet. Run npm install and npm run build in activity/."}, status=503)
        return web.FileResponse(page)

    async def observer_icon(self, request: web.Request) -> web.StreamResponse:
        icon = ACTIVITY_DIR / "dist" / "observer.png"
        if not icon.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(icon, headers={"Cache-Control": "public, max-age=86400"})

    async def observer_favicon(self, request: web.Request) -> web.StreamResponse:
        icon = ACTIVITY_DIR / "dist" / "observer-icon.svg"
        if not icon.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(icon, headers={"Cache-Control": "public, max-age=86400"})

    async def og_card(self, request: web.Request) -> web.StreamResponse:
        image = ACTIVITY_DIR / "dist" / "og-card.png"
        if not image.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(image, headers={"Cache-Control": "public, max-age=86400"})

    async def exchange_code(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, web.HTTPException):
            raise web.HTTPBadRequest(text="Invalid request body")
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not 1 <= len(code) <= 2048:
            raise web.HTTPBadRequest(text="Missing authorization code")
        assert self.session is not None
        try:
            async with self.session.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
            ) as response:
                data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("Activity OAuth token exchange failed")
            raise web.HTTPBadGateway(text="Discord authorization is temporarily unavailable")
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise web.HTTPUnauthorized(text="Discord did not authorize this Activity")
        return web.json_response({"access_token": token}, headers={"Cache-Control": "no-store"})

    async def viewer(self, request: web.Request) -> dict:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise web.HTTPUnauthorized(text="Sign in through Discord to continue")
        assert self.session is not None
        try:
            async with self.session.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": authorization},
            ) as response:
                if response.status != 200:
                    raise web.HTTPUnauthorized(text="Discord session expired; reopen the Activity")
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("Activity identity verification failed")
            raise web.HTTPBadGateway(text="Could not verify your Discord account")

    async def authorized_guild(self, request: web.Request, guild_id: int):
        user = await self.viewer(request)
        if int(user.get("id", 0)) == BOT_OWNER_ID:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                raise web.HTTPNotFound(text="Observer is not in that server")
            return guild, user
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text="Observer is not in that server")
        if guild.owner_id == int(user.get("id", 0)):
            return guild, user
        try:
            member = await guild.fetch_member(int(user["id"]))
        except (aiohttp.ClientError, ValueError, KeyError, Exception) as error:
            # A missing member and insufficient bot permissions both fail closed.
            if isinstance(error, web.HTTPException):
                raise
            raise web.HTTPForbidden(text="You need to be a server moderator or configured staff member")
        perms = member.guild_permissions
        if any((perms.administrator, perms.manage_guild, perms.moderate_members, perms.ban_members, perms.kick_members, perms.manage_messages)):
            return guild, user
        role_ids = {role.id for role in member.roles}
        store = self.bot.setup_store
        for module, key in (("moderation", "mod_role"), ("moderation", "head_mod_role"), ("bot", "staff_role")):
            try:
                role_id = int(store.get(guild_id, module, key))
            except (TypeError, ValueError):
                continue
            if role_id in role_ids:
                return guild, user
        raise web.HTTPForbidden(text="You need to be a server moderator or configured staff member")

    @staticmethod
    def guild_id(request: web.Request) -> int:
        try:
            value = int(request.query.get("guild_id", ""))
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            raise web.HTTPBadRequest(text="A valid server ID is required")

    async def activity_data(self, request: web.Request) -> web.Response:
        view = request.match_info["view"]
        if view not in {"overview", "reports", "cases", "bot-status"}:
            raise web.HTTPNotFound(text="Unknown staff view")
        guild_id = self.guild_id(request)
        guild, user = await self.authorized_guild(request, guild_id)
        now = monotonic()
        latency = max(0, round(self.bot.latency * 1000))
        uptime_seconds = int(now - self.started)
        uptime = f"{uptime_seconds // 86400}d {(uptime_seconds % 86400) // 3600}h {(uptime_seconds % 3600) // 60}m"
        with connect_sqlite(DB_PATH) as db:
            open_reports = int(db.execute("SELECT COUNT(*) FROM moderation_cases WHERE guild_id=? AND case_type='report' AND status='open'", (guild_id,)).fetchone()[0])
            open_cases = int(db.execute("SELECT COUNT(*) FROM moderation_cases WHERE guild_id=? AND status='open'", (guild_id,)).fetchone()[0])
            total_cases = int(db.execute("SELECT COUNT(*) FROM moderation_cases WHERE guild_id=?", (guild_id,)).fetchone()[0])
            if view == "cases":
                rows = db.execute("SELECT case_number,case_type,target_id,moderator_id,reason,status,created_at FROM moderation_cases WHERE guild_id=? ORDER BY id DESC LIMIT 50", (guild_id,)).fetchall()
                cases = [dict(row) for row in rows]
                for case in cases:
                    member = guild.get_member(int(case["target_id"]))
                    case["target_name"] = member.display_name if member else str(case["target_id"])
            if view in {"overview", "reports"}:
                rows = db.execute("SELECT case_number,target_id,reason,status,created_at FROM moderation_cases WHERE guild_id=? AND case_type='report' AND status='open' ORDER BY created_at ASC LIMIT 50", (guild_id,)).fetchall()
                reports = [dict(row) for row in rows]
                for report in reports:
                    member = guild.get_member(int(report["target_id"]))
                    report["target_name"] = member.display_name if member else str(report["target_id"])
        result = {"latency_ms": latency, "uptime": uptime, "open_reports": open_reports, "open_cases": open_cases, "server_count": len(self.bot.guilds)}
        if view in {"overview", "reports"}:
            result["reports"] = reports
        if view == "cases":
            result.update({"cases": cases, "total_cases": total_cases})
        if view == "bot-status" and int(user.get("id", 0)) == BOT_OWNER_ID:
            recent = list(getattr(getattr(self.bot, "error_reporter", None), "recent_errors", ()))
            result["errors"] = [{"id": e.error_id, "source": e.source, "type": e.exception, "location": e.location, "time": datetime.fromtimestamp(e.occurred_at, timezone.utc).strftime("%H:%M:%S UTC")} for e in reversed(recent[-12:])]
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "online", "servers": len(self.bot.guilds)})
