"""HTTP backend and static host for Observer's Discord Activity."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from cogs.config import BOT_OWNER_ID
from cogs.setup_ui import DB_PATH
from database import connect_sqlite

logger = logging.getLogger("observer.activity")
ACTIVITY_DIR = Path(__file__).resolve().parent / "activity"


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
        app.router.add_get("/api/public-info", self.public_info)
        app.router.add_post("/api/token", self.exchange_code)
        app.router.add_get("/api/activity/{view}", self.activity_data)
        app.router.add_get("/healthz", self.health)
        app.router.add_get("/", self.index)
        app.router.add_get("/staff", self.index)
        app.router.add_get("/setup", self.index)
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
            key = secrets.token_urlsafe(32)
            self.sessions[key] = {
                "user": user,
                "access_token": access_token,
                "refresh_token": tokens.get("refresh_token"),
                "expires_at": monotonic() + int(tokens.get("expires_in", 3600)),
                "created_at": monotonic(),
            }
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.exception("Discord OAuth callback failed")
            raise web.HTTPBadGateway(text="Discord sign-in is temporarily unavailable")
        response = web.HTTPFound("/")
        response.del_cookie("observer_oauth_state", path="/")
        response.set_cookie("observer_session", key, max_age=7 * 24 * 3600, httponly=True, secure=self._secure_cookie(), samesite="Lax", path="/")
        return response

    async def public_info(self, request: web.Request) -> web.Response:
        support_guild_id = os.getenv("OBSERVER_SUPPORT_GUILD_ID", "1535974879554437210")
        info = {
            "creator": os.getenv("OBSERVER_CREATOR_NAME", "").strip() or "Independent community project",
            "support_url": os.getenv("OBSERVER_SUPPORT_URL", f"https://discord.com/channels/{support_guild_id}"),
            "source_url": os.getenv("OBSERVER_SOURCE_URL", "").strip(),
        }
        return web.json_response(info, headers={"Cache-Control": "public, max-age=300"})

    def _get_session(self, request: web.Request) -> dict:
        key = request.cookies.get("observer_session", "")
        session = self.sessions.get(key)
        if session is None or monotonic() - session["created_at"] > 7 * 24 * 3600:
            self.sessions.pop(key, None)
            raise web.HTTPUnauthorized(text="Sign in with Discord to continue")
        return session

    async def session_info(self, request: web.Request) -> web.Response:
        key = request.cookies.get("observer_session", "")
        session = self.sessions.get(key)
        if session is None or monotonic() - session["created_at"] > 7 * 24 * 3600:
            self.sessions.pop(key, None)
            return web.json_response({"signed_in": False}, headers={"Cache-Control": "no-store"})
        user = session["user"]
        return web.json_response({
            "signed_in": True,
            "user": {"id": user.get("id"), "username": user.get("global_name") or user.get("username"), "avatar": user.get("avatar")},
        }, headers={"Cache-Control": "no-store"})

    async def _user_guilds(self, session: dict) -> list[dict]:
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
        owned = [guild for guild in await self._user_guilds(session) if guild.get("owner") is True]
        items = []
        for guild in owned:
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
            })
        items.sort(key=lambda item: (not item["bot_present"], item["name"].casefold()))
        return web.json_response({"servers": items, "invite_url": self._bot_invite_url()}, headers={"Cache-Control": "no-store"})

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
        response = web.json_response({"ok": True})
        response.del_cookie("observer_session", path="/")
        return response

    async def index(self, request: web.Request) -> web.StreamResponse:
        page = ACTIVITY_DIR / "dist" / "index.html"
        if not page.is_file():
            return web.json_response({"error": "Observer's website is not built yet. Run npm install and npm run build in activity/."}, status=503)
        return web.FileResponse(page)

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
