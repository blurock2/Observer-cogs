from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import discord
from discord import app_commands
from discord.ext import commands

from cogs.setup_ui import DB_PATH, SetupConfigStore
from database import ModerationCaseStore

MODULE_KEY = "security"
logger = logging.getLogger("observer.security")
RISK_WINDOW_SECONDS = 60
NEW_ACCOUNT_DAYS = 7
RAPID_JOIN_THRESHOLD = 5
INVITE_USE_THRESHOLD = 2
LOCKDOWN_TIMEOUT_MINUTES = 30
SCAM_ALERT_COOLDOWN_SECONDS = 60
SHORTENED_DOMAINS = {
    "bit.ly", "goo.gl", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "shorturl.at",
    "rebrand.ly", "buff.ly", "ow.ly", "rb.gy", "lnkd.in", "s.id", "soo.gd",
    "cutt.us", "tiny.cc", "qrco.de", "linktr.ee",
}
SCAM_PHRASES = (
    "free nitro", "claim nitro", "nitro gift", "verify your account",
    "verify to continue", "discord gift", "free steam",
    "claim your reward", "you have been selected", "exclusive giveaway",
    "scan to claim", "scan this qr", "connect your wallet", "sign this message",
    "discord moderator", "discord support", "account suspended",
    "account will be deleted", "security alert", "unusual login",
    "package waiting", "delivery confirmation", "update your address",
    "parcel tracking", "invoice overdue", "payment document", "payment failed",
    "billing information", "refund pending", "confirm your payment",
    "tax refund", "customs fee", "job offer", "work from home",
    "claim your prize", "verify your identity", "password reset",
)

URL_RE = re.compile(r"https?://[^\s<>()[\]{}]+", re.IGNORECASE)

DISCORD_MESSAGE_RE = re.compile(
    r"^https?://(?:www\.)?(?:discord\.com|discordapp\.com)/channels/"
    r"(?:@me|\d+)/\d+/\d+(?:[/?#].*)?$",
    re.IGNORECASE,
)

LOOKALIKE_RE = re.compile(r"(?:disc[o0]rd|nitr[o0]|ste[a4]m|free-?nitro)", re.IGNORECASE)
SUSPICIOUS_FILE_RE = re.compile(r"\.(?:exe|scr|bat|cmd|com|ps1|js|jse|vbs|vbe|hta|msi|dll|zip|rar|7z|iso)(?:$|[?#])", re.IGNORECASE)
REDIRECT_PARAM_RE = re.compile(r"(?:^|[?&])(url|u|target|dest|destination|redirect|redirect_url|continue|return_to)=", re.IGNORECASE)
OAUTH_PATH_RE = re.compile(r"/(?:oauth|authorize|authorization|connect|permissions?)(?:[/?.]|$)", re.IGNORECASE)
SCAM_PATH_RE = re.compile(
    r"/(?:invoice|payment|billing|refund|parcel|delivery|tracking|address|confirm|claim|prize|identity|password[-_]?reset)(?:[/_.?-]|$)",
    re.IGNORECASE,
)
SCAM_CONTEXT_RE = re.compile(
    r"(?:invoice|payment|billing|refund|parcel|delivery|tracking|address|customs|fee|prize|claim|identity|password|reset|verify|login)",
    re.IGNORECASE,
)
QR_PHRASE_RE = re.compile(r"(?:scan|camera).{0,30}(?:qr|code)|qr.{0,30}(?:claim|verify|reward|login)", re.IGNORECASE)
BRAND_HOST_RE = re.compile(r"(?:discord|steam|microsoft|google|roblox|paypal|binance|coinbase|metamask)", re.IGNORECASE)
TRUSTED_BRAND_DOMAINS = {
    "discord.com", "discordapp.com", "steampowered.com", "steamcommunity.com",
    "microsoft.com", "live.com", "google.com", "roblox.com", "paypal.com",
    "binance.com", "coinbase.com", "metamask.io",
}
GIF_DOMAINS = {"tenor.com", "klipy.com", "kippy.com"}
SUSPICIOUS_NAME_RE = re.compile(
    r"(?:free.?nitro|discord.?support|mod.?team|verify|gift|airdrop|admin)",
    re.IGNORECASE,
)


def detect_scam_signals(content: str) -> list[str]:
    """Return explainable scam indicators without deciding enforcement."""
    lowered = content.lower()
    phrase_signals = [
        phrase for phrase in SCAM_PHRASES
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", lowered)
    ]
    if "package is waiting" in lowered:
        phrase_signals.append("package waiting")
    signals = []
    if QR_PHRASE_RE.search(content) and re.search(
        r"\b(?:claim|verify|reward|login|wallet|nitro)\b", lowered
    ):
        signals.append("QR-code phishing bait")

    for raw_url in URL_RE.findall(content):
        raw_url = raw_url.rstrip(".,!?;:")

        if DISCORD_MESSAGE_RE.fullmatch(raw_url):
            continue

        try:
            parsed = urlsplit(raw_url)
            host = (parsed.hostname or "").lower().rstrip(".")
        except ValueError:
            signals.append("malformed URL")
            continue
        if not host:
            continue
        trusted_host = any(
            host == domain or host.endswith(f".{domain}")
            for domain in TRUSTED_BRAND_DOMAINS
        )
        # Official authentication/download paths are not impersonation.
        # Redirects may still merit an alert, but are not deletion evidence alone.
        if trusted_host:
            if parsed.username or parsed.password:
                signals.append("URL contains misleading credentials")
            if REDIRECT_PARAM_RE.search(parsed.query):
                signals.append(f"redirect or credential URL: {host}")
            continue
        decoded_url = unquote(raw_url)

        if host in GIF_DOMAINS or any(host.endswith(f".{domain}") for domain in GIF_DOMAINS):
            continue

        if host in SHORTENED_DOMAINS:
            signals.append(f"shortened link: {host}")
        if LOOKALIKE_RE.search(host) and not trusted_host:
            signals.append(f"lookalike domain: {host}")

        if "@" in parsed.netloc:
            signals.append("URL contains misleading credentials")

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            signals.append(f"raw IP address link: {host}")

        if host.startswith("xn--") or ".xn--" in host:
            signals.append(f"punycode domain: {host}")
        elif any(ord(character) > 127 for character in host):
            signals.append(f"non-ASCII domain: {host}")

        if host.count(".") >= 4:
            signals.append(f"deeply nested domain: {host}")

        if BRAND_HOST_RE.search(host) and not trusted_host:
            signals.append(f"brand impersonation domain: {host}")

        if SUSPICIOUS_FILE_RE.search(parsed.path + (f"?{parsed.query}" if parsed.query else "")):
            signals.append(f"suspicious download link: {host}")
        elif parsed.path.lower().endswith((".html", ".htm")) and SCAM_CONTEXT_RE.search(lowered):
            signals.append(f"suspicious scam webpage: {host}")
        if SCAM_PATH_RE.search(parsed.path) and SCAM_CONTEXT_RE.search(lowered):
            signals.append(f"suspicious scam path: {host}")
        if REDIRECT_PARAM_RE.search(parsed.query) or parsed.username or parsed.password:
            signals.append(f"redirect or credential URL: {host}")
        if OAUTH_PATH_RE.search(parsed.path) and any(
            phrase in lowered for phrase in ("verify", "connect", "authorize", "login", "permissions")
        ):
            signals.append(f"suspicious authorization link: {host}")
        if decoded_url != raw_url and any(
            phrase in decoded_url.lower() for phrase in ("login", "verify", "token", "password", "wallet")
        ):
            signals.append(f"encoded credential bait: {host}")
    # Ordinary discussion of these phrases is not enough to flag a message.
    if signals:
        signals = phrase_signals + signals
    return list(dict.fromkeys(signals))


class SecurityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.cases = ModerationCaseStore(DB_PATH)
        self.join_times: dict[int, deque[float]] = defaultdict(deque)
        self.lock_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._recovery_task: asyncio.Task | None = None
        self._permission_lock = asyncio.Lock()
        self.scam_alerts: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        self._recovery_task = asyncio.create_task(self._recover_locks())

    def cog_unload(self) -> None:
        if self._recovery_task:
            self._recovery_task.cancel()
        for task in self.lock_tasks.values():
            task.cancel()
        self.lock_tasks.clear()

    async def _recover_locks(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            records = self._get(guild.id, "channel_locks", {}) or {}
            for channel_id, record in records.items():
                if record.get("expires_at") is None:
                    continue
                channel = guild.get_channel(int(channel_id))
                if isinstance(channel, discord.TextChannel):
                    key = (guild.id, channel.id)
                    if key not in self.lock_tasks:
                        self.lock_tasks[key] = asyncio.create_task(
                            self._unlock_after(channel, max(0, record["expires_at"] - time.time()))
                        )

    async def _restore_lock(self, channel: discord.TextChannel) -> None:
        records = self._get(channel.guild.id, "channel_locks", {}) or {}
        record = records.get(str(channel.id))
        previous = record["send_messages"] if record else None
        await self._set_send_permission(channel, previous, "Security channel unlock")
        if record:
            records.pop(str(channel.id))
            self.store.set(channel.guild.id, MODULE_KEY, "channel_locks", records)

    async def _set_send_permission(
        self, channel: discord.TextChannel, value: bool | None, reason: str
    ) -> None:
        overwrite = channel.overwrites_for(channel.guild.default_role)
        overwrite.send_messages = value
        await channel.set_permissions(
            channel.guild.default_role,
            overwrite=None if overwrite.is_empty() else overwrite,
            reason=reason,
        )

    def _get(self, guild_id: int, key: str, default=None):
        return self.store.get(guild_id, MODULE_KEY, key, default)

    def _bool_setting(self, guild_id: int, key: str, default: bool = False) -> bool:
        value = self._get(guild_id, key, default)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        return bool(value)

    def _enabled(self, guild_id: int) -> bool:
        return self._bool_setting(guild_id, "enabled", True)

    def _get_role_id(self, guild_id: int, key: str) -> int | None:
        value = self._get(guild_id, key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _has_scam_bypass_role(self, message: discord.Message) -> bool:
        role_id = self._get_role_id(message.guild.id, "scam_bypass_role")
        return role_id is not None and any(
            role.id == role_id for role in getattr(message.author, "roles", ())
        )

    def _int_setting(self, guild_id: int, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self._get(guild_id, key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _moderator(self, interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.manage_channels
            or interaction.user.guild_permissions.manage_guild
        )

    async def _alert(self, guild: discord.Guild, title: str, description: str) -> None:
        channel_id = self.store.get(guild.id, "moderation", "log_channel")
        if channel_id is None:
            channel_id = self.store.get(guild.id, MODULE_KEY, "alert_channel")
        try:
            channel = guild.get_channel(int(channel_id))
        except (TypeError, ValueError):
            channel = None
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(
                embed=discord.Embed(title=title, description=description, color=0xED4245),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("Could not send security alert in guild %s", guild.id)

    def _risk_reasons(self, member: discord.Member) -> list[str]:
        reasons = []
        account_age = discord.utils.utcnow() - member.created_at
        new_account_days = self._int_setting(
            member.guild.id,
            "new_account_days",
            NEW_ACCOUNT_DAYS,
            0,
            365,
        )
        if account_age < timedelta(days=new_account_days):
            reasons.append(f"account is {account_age.days} day(s) old")
        if SUSPICIOUS_NAME_RE.search(f"{member.name} {member.display_name}"):
            reasons.append("suspicious display name")
        return reasons

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not self._enabled(member.guild.id):
            return
        now = time.monotonic()
        joins = self.join_times[member.guild.id]
        joins.append(now)
        join_window = self._int_setting(
            member.guild.id,
            "rapid_join_window_seconds",
            RISK_WINDOW_SECONDS,
            10,
            3600,
        )
        while joins and now - joins[0] > join_window:
            joins.popleft()
        reasons = self._risk_reasons(member)
        join_threshold = self._int_setting(
            member.guild.id,
            "rapid_join_threshold",
            RAPID_JOIN_THRESHOLD,
            2,
            100,
        )
        if len(joins) >= join_threshold:
            reasons.append(f"{len(joins)} joins in {join_window} seconds")
        invite_threshold = self._int_setting(
            member.guild.id,
            "invite_use_threshold",
            INVITE_USE_THRESHOLD,
            1,
            1000000,
        )
        try:
            for invite in await member.guild.invites():
                if invite.uses and invite.uses >= invite_threshold:
                    reasons.append(f"invite {invite.code} has been used {invite.uses} times")
        except discord.DiscordException:
            pass
        lockdown = self._bool_setting(member.guild.id, "lockdown_enabled", False)
        new_account_days = self._int_setting(
            member.guild.id,
            "new_account_days",
            NEW_ACCOUNT_DAYS,
            0,
            365,
        )
        auto_timeout = self._bool_setting(member.guild.id, "lockdown_auto_timeout", False)
        if (
            lockdown
            and auto_timeout
            and discord.utils.utcnow() - member.created_at < timedelta(days=new_account_days)
        ):
            try:
                timeout_minutes = self._int_setting(
                    member.guild.id,
                    "lockdown_new_account_timeout_minutes",
                    LOCKDOWN_TIMEOUT_MINUTES,
                    0,
                    40320,
                )
                if timeout_minutes > 0:
                    await member.timeout(timedelta(minutes=timeout_minutes), reason="Temporary anti-raid new-account restriction")
                reasons.append("lockdown restriction applied")
            except discord.DiscordException:
                reasons.append("lockdown restriction could not be applied")
        if reasons:
            await self._alert(
                member.guild,
                "Join risk detected",
                f"{member.mention} (`{member.id}`)\n" + "\n".join(f"- {reason}" for reason in reasons),
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not self._enabled(message.guild.id):
            return
        if self._has_scam_bypass_role(message):
            return
        signals = detect_scam_signals(message.content)
        if not signals:
            return
        now = time.monotonic()
        cooldown = self._int_setting(
            message.guild.id,
            "scam_alert_cooldown_seconds",
            SCAM_ALERT_COOLDOWN_SECONDS,
            0,
            3600,
        )
        alert_key = (message.guild.id, message.author.id)
        alert_due = not cooldown or now - self.scam_alerts.get(
            alert_key, float("-inf")
        ) >= cooldown
        if alert_due:
            self.scam_alerts[alert_key] = now
            self.cases.create_case(
                message.guild.id,
                "scam_flag",
                message.author.id,
                reason="; ".join(signals),
                source_message_id=message.id,
                status="open",
            )
        deletion_result = "Deletion disabled or insufficient evidence"
        strong_signal = any(
            signal.startswith((
                "lookalike domain:", "brand impersonation domain:",
                "URL contains misleading credentials", "encoded credential bait:",
                "suspicious scam webpage:", "suspicious scam path:",
                "QR-code phishing bait",
            )) for signal in signals
        )
        contextual_signal = any(signal in SCAM_PHRASES for signal in signals) and any(
            signal.startswith(("shortened link:", "suspicious download link:", "raw IP address link:", "suspicious authorization link:"))
            for signal in signals
        )
        if self._bool_setting(message.guild.id, "delete_scam_messages", False) and (strong_signal or contextual_signal):
            try:
                try:
                    await message.delete(reason="Possible scam message flagged")
                except TypeError:
                    # Some Discord message-like objects, including partial
                    # messages, do not accept the optional audit-log reason.
                    await message.delete()
            except discord.Forbidden:
                deletion_result = "Deletion failed: bot needs Manage Messages"
                logger.warning(
                    "Could not delete flagged scam message %s in guild %s: missing permissions",
                    message.id,
                    message.guild.id,
                )
            except discord.NotFound:
                deletion_result = "Message was already deleted"
            except discord.HTTPException as error:
                deletion_result = f"Deletion failed: Discord error {error.status}"
                logger.warning(
                    "Could not delete flagged scam message %s in guild %s: %s",
                    message.id,
                    message.guild.id,
                    error,
                )
            else:
                deletion_result = "Message deleted"
        if not alert_due:
            return
        await self._alert(
            message.guild,
            "Possible scam message",
            f"Author: {message.author.mention}\nChannel: {message.channel.mention}\n"
            f"[Jump to message]({message.jump_url})\nSignals: {', '.join(signals)}\n"
            f"Action: {deletion_result}",
        )

    async def _set_channel_lock(self, channel: discord.TextChannel, locked: bool) -> None:
        await self._set_send_permission(
            channel, False if locked else None,
            "Security channel lock" if locked else "Security channel unlock",
        )

    @app_commands.command(name="slowmode", description="Set a channel's slowmode in seconds.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, channel: discord.TextChannel, seconds: int) -> None:
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message("Seconds must be between 0 and 21600.", ephemeral=True)
            return
        await channel.edit(slowmode_delay=seconds, reason=f"Slowmode set by {interaction.user}")
        await interaction.response.send_message(f"Slowmode for {channel.mention}: {seconds}s.", ephemeral=True)

    @app_commands.command(name="lock", description="Temporarily lock a channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel, seconds: int | None = None) -> None:
        if seconds is not None and not 1 <= seconds <= 86400:
            await interaction.response.send_message("The temporary lock must be 1 to 86400 seconds.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self._permission_lock:
            if self._get(channel.guild.id, "lockdown_previous"):
                await interaction.followup.send("Disable lockdown before changing individual locks.", ephemeral=True)
                return
            key = (channel.guild.id, channel.id)
            records = self._get(channel.guild.id, "channel_locks", {}) or {}
            record = records.get(str(channel.id), {
                "send_messages": channel.overwrites_for(channel.guild.default_role).send_messages,
            })
            record["expires_at"] = time.time() + seconds if seconds is not None else None
            records[str(channel.id)] = record
            # Save recovery state before making any Discord changes.
            self.store.set(channel.guild.id, MODULE_KEY, "channel_locks", records)
            old_task = self.lock_tasks.pop(key, None)
            if old_task:
                old_task.cancel()
            try:
                await self._set_channel_lock(channel, True)
            finally:
                if seconds is not None:
                    self.lock_tasks[key] = asyncio.create_task(self._unlock_after(channel, seconds))
            await interaction.followup.send(f"Locked {channel.mention}." + (f" Unlocking in {seconds}s." if seconds else ""), ephemeral=True)

    async def _unlock_after(self, channel: discord.TextChannel, seconds: int) -> None:
        try:
            await asyncio.sleep(seconds)
            while True:
                try:
                    async with self._permission_lock:
                        await self._restore_lock(channel)
                    break
                except discord.NotFound:
                    records = self._get(channel.guild.id, "channel_locks", {}) or {}
                    records.pop(str(channel.id), None)
                    self.store.set(channel.guild.id, MODULE_KEY, "channel_locks", records)
                    break
                except discord.HTTPException:
                    logger.exception("Temporary unlock failed for channel %s; retrying", channel.id)
                    await asyncio.sleep(60)
        finally:
            key = (channel.guild.id, channel.id)
            if self.lock_tasks.get(key) is asyncio.current_task():
                self.lock_tasks.pop(key, None)

    @app_commands.command(name="unlock", description="Unlock a channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._permission_lock:
            if self._get(channel.guild.id, "lockdown_previous"):
                await interaction.followup.send("Disable lockdown before changing individual locks.", ephemeral=True)
                return
            key = (channel.guild.id, channel.id)
            await self._restore_lock(channel)
            task = self.lock_tasks.pop(key, None)
            if task:
                task.cancel()
            await interaction.followup.send(f"Unlocked {channel.mention}.", ephemeral=True)


    @app_commands.command(name="lockdown", description="Enable or disable temporary anti-raid lockdown.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(action="Enable or disable lockdown.")
    @app_commands.choices(action=[
        app_commands.Choice(name="Enable", value="enable"),
        app_commands.Choice(name="Disable", value="disable"),
    ])
    async def lockdown(self, interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
        if interaction.guild is None:
            return
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        async with self._permission_lock:
            if action.value == "enable":
                if self._get(guild.id, "lockdown_previous"):
                    await interaction.followup.send("Lockdown already has recovery state. Disable it before enabling again.", ephemeral=True)
                    return
                if self._get(guild.id, "channel_locks", {}):
                    await interaction.followup.send("Unlock individual channels before enabling lockdown.", ephemeral=True)
                    return
                channels = [
                    channel for channel in guild.text_channels
                    if channel.name.lower() not in {"rules", "announcements", "staff", "reports"}
                ]
                previous = {
                    "verification": guild.verification_level.name,
                    "channels": [
                        {"id": channel.id, "send_messages": channel.overwrites_for(guild.default_role).send_messages}
                        for channel in channels
                    ],
                }
                # Persist the complete restoration plan before the first API call.
                self.store.set(guild.id, MODULE_KEY, "lockdown_previous", previous)
                self.store.set(guild.id, MODULE_KEY, "lockdown_enabled", True)
                try:
                    for channel in channels:
                        await self._set_channel_lock(channel, True)
                    if guild.verification_level < discord.VerificationLevel.high:
                        await guild.edit(verification_level=discord.VerificationLevel.high)
                except discord.DiscordException:
                    logger.exception("Lockdown partially applied in guild %s", guild.id)
                    await interaction.followup.send("Lockdown could not finish. Recovery data is saved; disable lockdown to restore permissions.", ephemeral=True)
                    return
                self.cases.create_case(guild.id, "lockdown", interaction.user.id, interaction.user.id, "Enabled", status="resolved")
                await self._alert(guild, "Anti-raid lockdown enabled", f"Enabled by {interaction.user.mention}.")
                await interaction.followup.send("Lockdown enabled; channels are locked and verification is at least High.", ephemeral=True)
                return
            previous = self._get(guild.id, "lockdown_previous", {}) or {}
            if not previous:
                await interaction.followup.send("There is no saved lockdown to restore.", ephemeral=True)
                return
            failed = []
            for item in previous.get("channels", []):
                channel = guild.get_channel(item.get("id"))
                if isinstance(channel, discord.TextChannel):
                    try:
                        await self._set_send_permission(channel, item.get("send_messages"), "Anti-raid lockdown disabled")
                    except discord.NotFound:
                        continue
                    except discord.HTTPException:
                        logger.exception("Could not restore lockdown channel %s", channel.id)
                        failed.append(item)
            verification_failed = False
            try:
                await guild.edit(verification_level=discord.VerificationLevel[previous["verification"]])
            except (discord.DiscordException, KeyError):
                logger.exception("Could not restore verification level for guild %s", guild.id)
                verification_failed = True
            if failed or verification_failed:
                previous["channels"] = failed
                self.store.set(guild.id, MODULE_KEY, "lockdown_previous", previous)
                await interaction.followup.send("Some settings could not be restored. Recovery data was kept; fix bot permissions and disable lockdown again.", ephemeral=True)
                return
            self.store.delete(guild.id, MODULE_KEY, "lockdown_previous")
            self.store.set(guild.id, MODULE_KEY, "lockdown_enabled", False)
            self.cases.create_case(guild.id, "lockdown", interaction.user.id, interaction.user.id, "Disabled", status="resolved")
            await self._alert(guild, "Anti-raid lockdown disabled", f"Disabled by {interaction.user.mention}.")
            await interaction.followup.send("Lockdown disabled and saved channel permissions restored.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SecurityCog(bot))
