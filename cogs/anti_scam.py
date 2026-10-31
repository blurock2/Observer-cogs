from __future__ import annotations

from error_handling import ObserverCog

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import tempfile
import time
from collections import OrderedDict, defaultdict, deque
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import unquote, urlsplit

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageOps
from io import BytesIO

try:
    import pytesseract
except ImportError:  # OCR can be enabled by installing the optional dependency.
    pytesseract = None
else:
    # Prefer an explicit path when configured; the Windows default matches the
    # standard installer location and avoids depending on the bot process PATH.
    pytesseract.pytesseract.tesseract_cmd = os.getenv(
        "TESSERACT_CMD",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else "tesseract",
    )

# Avoid CPU oversubscription when several images are processed together.
# Preserve an explicit administrator override.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

try:
    import cv2
    import numpy as np
except ImportError:  # QR scanning is optional on systems without OpenCV.
    cv2 = None
    np = None

from cogs.setup_ui import DB_PATH, SetupConfigStore, owner_or_has_guild_permissions
from cogs.config import is_bot_owner
from cogs.report_msg import AcceptedReportStore
from database import AntiScamActivityStore, ModerationActionStore, ModerationCaseStore
from error_handling import log_error
from moderation_notifications import dm_moderation_action

MODULE_KEY = "security"
logger = logging.getLogger("observer.security")
ANTI_RAID_MODULE_KEY = "anti_raid"
RAID_SETTING_KEYS = {
    "enabled": "raid_enabled",
    "join_threshold": "raid_join_threshold",
    "join_window_seconds": "raid_join_window_seconds",
    "hold_seconds": "raid_hold_seconds",
    "timeout_minutes": "raid_timeout_minutes",
    "alert_channel": "raid_alert_channel",
    "bypass_role": "raid_bypass_role",
}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCAM_IMAGE_HASHES = DATA_DIR / "scam_image_hashes.json"
IMAGE_SCAN_MAX_BYTES = 8 * 1024 * 1024
IMAGE_SCAN_MAX_PIXELS = 25_000_000
IMAGE_HASH_DISTANCE = 10
IMAGE_FILE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"
)
OCR_WARNING_REPORTED = False
OCR_MAX_SIDE = 1800
OCR_PASS_TIMEOUT = 3.0
OCR_TOTAL_BUDGET = 5.0
OCR_CACHE_TTL = 600
OCR_CACHE_MAX_ITEMS = 256
OCR_MAX_WORKERS = 2
DISCORD_IMAGE_PROXY_HOST_RE = re.compile(
    r"^(?:media|images-ext-\d+)\.discordapp\.net$", re.IGNORECASE
)
NEW_ACCOUNT_DAYS = 7
INVITE_USE_THRESHOLD = 2
LOCKDOWN_TIMEOUT_MINUTES = 30
SCAM_ALERT_COOLDOWN_SECONDS = 60
# Keep the image-only quarantine short: it exists to catch a scam image burst
# while OCR finishes, not to suppress all of a sender's messages for ten minutes.
SCAM_SENDER_QUARANTINE_SECONDS = 60
SCAM_UNCERTAIN_TIMEOUT_MINUTES = 1440


class NotScamImageView(discord.ui.View):
    def __init__(self, cog: "AntiScam", hashes: list[dict[str, str]]):
        super().__init__(timeout=30 * 24 * 60 * 60)
        self.cog = cog
        self.hashes = hashes

    @discord.ui.button(label="Not a scam image", style=discord.ButtonStyle.success)
    async def mark_good(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._not_a_scam_callback(interaction, self.hashes)
SHORTENED_DOMAINS = {
    "bit.ly", "goo.gl", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "shorturl.at",
    "rebrand.ly", "buff.ly", "ow.ly", "rb.gy", "lnkd.in", "s.id", "soo.gd",
    "cutt.us", "tiny.cc", "qrco.de", "linktr.ee",
}
SCAM_PHRASES = (
    "free nitro", "claim nitro", "nitro gift", "verify your account",
    "you've been gifted a subscription", "you've been gifted nitro",
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
MRBEAST_IMAGE_RE = re.compile(r"mr\s*beast", re.IGNORECASE)
GIVEAWAY_BAIT_RE = re.compile(
    r"\b(?:first|last|only)\s+\d+\s+people\b|"
    r"\b\d+\s*(?:dollars|usd)\b|\$\s?\d+|"
    r"\b(?:giveaway|free\s+(?:money|cash|prize))\b",
    re.IGNORECASE,
)
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


def message_embed_text(message: discord.Message) -> str:
    """Collect user-visible embed text, including fields hidden from content."""
    parts: list[str] = []
    for embed in getattr(message, "embeds", ()):
        for value in (
            getattr(embed, "title", None),
            getattr(embed, "description", None),
            getattr(getattr(embed, "author", None), "name", None),
            getattr(getattr(embed, "footer", None), "text", None),
        ):
            if value:
                parts.append(str(value))
        for field in getattr(embed, "fields", ()):
            for value in (getattr(field, "name", None), getattr(field, "value", None)):
                if value:
                    parts.append(str(value))
    return "\n".join(parts)


def is_image_attachment(attachment) -> bool:
    """Check image metadata defensively; malformed MIME values must not stop listeners."""
    content_type = getattr(attachment, "content_type", None)
    filename = getattr(attachment, "filename", None)
    return (
        isinstance(content_type, str) and content_type.lower().startswith("image/")
    ) or (
        isinstance(filename, str)
        and filename.lower().endswith(IMAGE_FILE_EXTENSIONS)
    )


def detect_scam_giveaway_signals(text: str) -> list[str]:
    """Detect Nitro prize claims that require adding a bot or waiting for a DM."""
    lowered = re.sub(r"\s+", " ", text.lower())
    claims_prize = bool(re.search(
        r"\b(?:you\s+(?:have\s+)?won|you['’]ve\s+won|congratulations|you['’]ve\s+been\s+selected)\b",
        lowered,
    ))
    mentions_nitro = "nitro" in lowered and any(
        term in lowered for term in ("discord", "giveaway", "prize", "reward")
    )
    asks_to_add_bot = bool(re.search(
        r"\b(?:add|invite)\s+(?:this|the|our)?\s*bot\b", lowered
    ))
    promises_later_reward = bool(re.search(
        r"\bwait\s+(?:\d+\s*)?(?:hours?|days?)\b|reward\s+in\s+(?:a\s+)?private\s+message",
        lowered,
    ))
    if claims_prize and mentions_nitro and (asks_to_add_bot or promises_later_reward):
        return ["suspicious Nitro giveaway embed: prize claim requires adding a bot or waiting for a private reward"]
    return []


def detect_crypto_giveaway_signals(text: str) -> list[str]:
    """Flag celebrity crypto giveaways that include a link or participation CTA."""
    normalized = re.sub(r"\s+", " ", text.lower())
    impersonated = bool(re.search(r"\b(?:elon\s+musk|tesla|spacex|mr\s*beast)\b", normalized))
    crypto = bool(re.search(
        r"\b(?:cryptocurrency|crypto|bitcoin|btc|ethereum|ether|eth|dogecoin|doge|usdt)\b",
        normalized,
    ))
    giveaway = bool(re.search(
        r"\b(?:giveaway|airdrop|giving\s+away|crypto\s+party|casino|bonus|promo\s+code)\b",
        normalized,
    ))
    link_or_cta = bool(
        URL_RE.search(normalized)
        or re.search(r"\b(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s]*)?", normalized)
        or re.search(
        r"\b(?:participate|send\s+(?:bitcoin|btc|ethereum|eth)|"
        r"double\s+your|send\s+.*\s+receive|contribution\s+address|"
        r"navigate\s+to\s+the\s+(?:btc|bitcoin|crypto)\s+portal)\b",
        normalized,
        )
    )
    if impersonated and crypto and giveaway and link_or_cta:
        return ["celebrity crypto giveaway scam pattern"]
    return []


def detect_nitro_gift_image_signals(text: str) -> list[str]:
    """Catch fake gift-card screenshots that imitate Discord's Nitro claim UI."""
    normalized = re.sub(r"\s+", " ", text.lower())
    gifted = bool(re.search(
        r"you['’]?ve\s+been\s+gifted.{0,60}(?:nitro|subscription)|"
        r"gifted\s+nitro.{0,40}(?:month|subscription)",
        normalized,
    ))
    has_claim = bool(re.search(r"\bclaim\b", normalized))
    has_expiry = bool(re.search(r"\bexpires?\b.{0,30}\b(?:day|hour|month|week)", normalized))
    has_disclaimer = "disclaimer" in normalized
    if gifted and has_claim and (has_expiry or has_disclaimer):
        return ["suspicious Nitro gift claim image"]
    return []


def image_ocr_text_signals(text: str) -> list[str]:
    """Evaluate OCR text, including domains whose scheme was not recognised."""
    normalized = re.sub(r"\s+", " ", text).strip()
    signals = []
    signals.extend(detect_scam_giveaway_signals(normalized))
    signals.extend(detect_crypto_giveaway_signals(normalized))
    signals.extend(detect_nitro_gift_image_signals(normalized))
    signals.extend(f"image text: {signal}" for signal in detect_scam_signals(normalized))
    # A screenshot address bar often contains just a hostname. Recognise whole
    # hostnames; do not silently fix spelling or invent domains across spaces.
    domains = re.findall(
        r"(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z]{2,24}(?![\w.-])", normalized.lower(),
    )
    for domain in dict.fromkeys(domains):
        # Exclude official hosts and subdomains through the existing URL rules.
        if not (LOOKALIKE_RE.search(domain) or BRAND_HOST_RE.search(domain)):
            continue
        signals.extend(
            f"image text: {signal}"
            for signal in detect_scam_signals(f"https://{domain}")
            if signal.startswith(("lookalike domain:", "brand impersonation domain:"))
        )
    if MRBEAST_IMAGE_RE.search(normalized) and GIVEAWAY_BAIT_RE.search(normalized):
        signals.append("MrBeast-style image giveaway bait")
    for phrase in SCAM_PHRASES:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized.lower()):
            signals.append(f"scam phrase in image: {phrase}")
    return list(dict.fromkeys(signals))


class AntiScam(ObserverCog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)
        self.cases = ModerationCaseStore(DB_PATH)
        self.actions = ModerationActionStore(DB_PATH)
        self.report_store = AcceptedReportStore(DB_PATH)
        self.activity_store = AntiScamActivityStore(DB_PATH)
        self.lock_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._recovery_task: asyncio.Task | None = None
        self._permission_lock = asyncio.Lock()
        self.scam_alerts: dict[tuple[int, int], float] = {}
        self.scam_enforcements: dict[tuple[int, int], float] = {}
        self.cross_server_enforcements: dict[tuple[int, int], float] = {}
        self.cross_server_check_cache: dict[tuple[int, int], float] = {}
        self.scam_user_locks = defaultdict(asyncio.Lock)
        self.scam_quarantine: dict[tuple[int, int], float] = {}
        self.recent_image_messages = defaultdict(lambda: deque(maxlen=20))
        self.recent_image_fingerprints = defaultdict(lambda: deque(maxlen=20))
        self.recent_user_activity = defaultdict(lambda: deque(maxlen=50))
        self.activity_history_loaded: set[tuple[int, int]] = set()
        self.activity_load_locks = defaultdict(asyncio.Lock)
        self.joins = defaultdict(lambda: deque(maxlen=1000))
        self.raid_locks = defaultdict(asyncio.Lock)
        self.trapped = set()
        self.last_case_ids = {}
        self.last_action_ids = {}
        self._scam_hash_lock = asyncio.Lock()
        self._image_scan_slots = asyncio.Semaphore(OCR_MAX_WORKERS)
        # Raw extraction only: guild-specific rules/fingerprints are re-evaluated.
        self._image_scan_cache = OrderedDict()
        self._image_scan_tasks: dict[str, asyncio.Task] = {}
        self.global_scam_image_hashes, self.guild_scam_image_hashes = (
            self._load_scam_image_hashes()
        )
        self.global_good_image_hashes, self.guild_good_image_hashes = (
            self._load_good_image_hashes()
        )

    @staticmethod
    def _valid_image_hashes(value) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        return [
            item for item in value
            if isinstance(item, dict)
            and isinstance(item.get("hash"), str)
            and len(item["hash"]) == 16
        ]

    def _load_scam_image_hashes(
        self,
    ) -> tuple[list[dict[str, str]], dict[str, list[dict[str, str]]]]:
        try:
            value = json.loads(SCAM_IMAGE_HASHES.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [], {}
        except json.JSONDecodeError:
            logger.exception(
                "Trained scam image file is invalid JSON: %s", SCAM_IMAGE_HASHES
            )
            return [], {}
        except OSError:
            logger.exception(
                "Could not read trained scam image file: %s", SCAM_IMAGE_HASHES
            )
            return [], {}
        # Older versions stored one shared list; keep those fingerprints global.
        if isinstance(value, list):
            global_hashes = self._valid_image_hashes(value)
            logger.info(
                "Loaded %s trained scam image fingerprint(s) from %s",
                len(global_hashes), SCAM_IMAGE_HASHES,
            )
            return global_hashes, {}
        if not isinstance(value, dict):
            logger.error("Trained scam image file has an unsupported format: %s", SCAM_IMAGE_HASHES)
            return [], {}
        guilds = value.get("guilds", {})
        valid_guilds = {
            str(guild_id): self._valid_image_hashes(hashes)
            for guild_id, hashes in guilds.items()
            if isinstance(guilds, dict)
        } if isinstance(guilds, dict) else {}
        global_hashes = self._valid_image_hashes(value.get("global", []))
        guild_hash_count = sum(len(items) for items in valid_guilds.values())
        logger.info(
            "Loaded %s global and %s server-specific scam image fingerprint(s) from %s",
            len(global_hashes), guild_hash_count, SCAM_IMAGE_HASHES,
        )
        return global_hashes, valid_guilds

    def _save_scam_image_hashes(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=DATA_DIR, suffix=".tmp"
        ) as output:
            json.dump(
                {
                    "global": self.global_scam_image_hashes,
                    "guilds": self.guild_scam_image_hashes,
                    "good_global": self.global_good_image_hashes,
                    "good_guilds": self.guild_good_image_hashes,
                },
                output,
                indent=2,
            )
            output.flush()
            os.fsync(output.fileno())
            temp_path = Path(output.name)
        os.replace(temp_path, SCAM_IMAGE_HASHES)

    def _load_good_image_hashes(self):
        try:
            value = json.loads(SCAM_IMAGE_HASHES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [], {}
        if not isinstance(value, dict):
            return [], {}
        guilds = value.get("good_guilds", {})
        valid_guilds = {
            str(guild_id): self._valid_image_hashes(items)
            for guild_id, items in guilds.items()
        } if isinstance(guilds, dict) else {}
        return self._valid_image_hashes(value.get("good_global", [])), valid_guilds

    def _image_is_trained_good(self, image_hash: str, image_bytes: bytes, guild_id: int | None) -> bool:
        known = list(self.global_good_image_hashes)
        if guild_id is not None:
            known.extend(self.guild_good_image_hashes.get(str(guild_id), ()))
        sha256 = hashlib.sha256(image_bytes).hexdigest()
        return any(
            item.get("sha256") == sha256
            or self._hash_distance(image_hash, item["hash"]) <= IMAGE_HASH_DISTANCE
            for item in known
        )

    async def _train_good_image_hashes(self, guild_id: int, hashes: list[dict[str, str]]) -> int:
        if not hashes:
            return 0
        async with self._scam_hash_lock:
            trained = self.guild_good_image_hashes.setdefault(str(guild_id), [])
            known = {item.get("sha256") for item in trained}
            added = [item for item in hashes if item.get("sha256") not in known]
            trained.extend(added)
            if added:
                await asyncio.to_thread(self._save_scam_image_hashes)
        return len(added)

    async def _not_a_scam_callback(self, interaction: discord.Interaction, hashes: list[dict[str, str]]) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("Manage Messages permission is required.", ephemeral=True)
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("This action is only available in a server.", ephemeral=True)
            return
        count = await self._train_good_image_hashes(interaction.guild_id, hashes)
        await interaction.response.send_message(
            f"Marked this as a good image. Added {count} benign image fingerprint(s); matching images will no longer be flagged by image scanning in this server.",
            ephemeral=True,
        )

    @staticmethod
    def _average_hash(image_bytes: bytes) -> str:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.width * source.height > IMAGE_SCAN_MAX_PIXELS:
                raise ValueError("Image dimensions are too large to scan")
            # Pillow warns when a palette image with byte-based transparency is
            # converted directly. Normalize its palette and transparency first.
            if source.mode == "P":
                with source.convert("RGBA") as normalized:
                    grayscale = normalized.convert("L")
            else:
                grayscale = source.convert("L")
            pixels = list(grayscale.resize((8, 8)).getdata())
        average = sum(pixels) / len(pixels)
        value = sum((1 << index) for index, pixel in enumerate(pixels) if pixel >= average)
        return f"{value:016x}"

    @staticmethod
    def _hash_distance(first: str, second: str) -> int:
        try:
            return (int(first, 16) ^ int(second, 16)).bit_count()
        except (TypeError, ValueError):
            return 64

    @staticmethod
    def _ocr_has_scam_evidence(text: str) -> bool:
        # Stop early only for multi-part patterns or domain evidence, not a
        # generic phrase which may occur in an ordinary discussion.
        return any(not signal.startswith("scam phrase in image:")
                   for signal in image_ocr_text_signals(text))

    def _scan_image_worker(self, image_bytes: bytes) -> tuple[str, list[str], bool]:
        """Two bounded OCR passes; return whether extraction finished normally."""
        global OCR_WARNING_REPORTED
        started = time.monotonic()
        with Image.open(BytesIO(image_bytes)) as source:
            if source.width * source.height > IMAGE_SCAN_MAX_PIXELS:
                raise ValueError("Image dimensions are too large to scan")
            # Palette PNGs can carry transparency as raw bytes; Pillow warns
            # when converting those directly to RGB, so normalize alpha first.
            if source.mode == "P":
                with source.convert("RGBA") as normalized:
                    normalized.thumbnail(
                        (OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS
                    )
                    rgb = normalized.convert("RGB")
            else:
                # Bound decoded output before expensive conversion/preprocessing.
                source.thumbnail(
                    (OCR_MAX_SIDE, OCR_MAX_SIDE), Image.Resampling.LANCZOS
                )
                rgb = source.convert("RGB")
        recognized: list[str] = []
        passes = 0
        complete = True
        if pytesseract is None:
            complete = False
            if not OCR_WARNING_REPORTED:
                logger.warning("Image OCR unavailable: install pytesseract and Tesseract")
                OCR_WARNING_REPORTED = True
        else:
            for pass_index in range(2):
                remaining = OCR_TOTAL_BUDGET - (time.monotonic() - started)
                if remaining <= 0:
                    complete = False
                    logger.warning("Image OCR budget exhausted; result is incomplete")
                    break
                if pass_index == 0:
                    variant = ImageOps.grayscale(rgb)
                else:
                    # One cleanup retry, created only when needed. Small images
                    # get enlarged here instead of every image being enlarged.
                    variant = ImageOps.grayscale(rgb)
                    if variant.resize((1, 1)).getpixel((0, 0)) < 127:
                        # Dark Discord screenshots with large redaction blocks
                        # can be judged empty by Tesseract. Isolate bright text
                        # and place it on white for the fallback pass.
                        variant = variant.point(lambda pixel: 0 if pixel > 130 else 255)
                    else:
                        variant = ImageOps.autocontrast(variant)
                    if max(variant.size) < 1200:
                        factor = min(2.0, OCR_MAX_SIDE / max(variant.size))
                        variant = variant.resize(
                            (max(1, round(variant.width * factor)),
                             max(1, round(variant.height * factor))),
                            Image.Resampling.LANCZOS,
                        )
                    variant = ImageOps.expand(variant, border=10, fill=255)
                remaining = OCR_TOTAL_BUDGET - (time.monotonic() - started)
                if remaining <= 0:
                    complete = False
                    logger.warning("Image OCR budget exhausted; result is incomplete")
                    break
                passes += 1
                try:
                    # Sparse text suits screenshots/posters; block mode is the
                    # fallback. pytesseract kills its subprocess on timeout.
                    found = pytesseract.image_to_string(
                        variant,
                        config=f"--psm {11 if pass_index == 0 else 6}",
                        timeout=min(OCR_PASS_TIMEOUT, remaining),
                    )
                except pytesseract.pytesseract.TesseractNotFoundError:
                    complete = False
                    if not OCR_WARNING_REPORTED:
                        logger.warning("Tesseract executable unavailable; check TESSERACT_CMD")
                        OCR_WARNING_REPORTED = True
                    break
                except RuntimeError as error:
                    complete = False
                    if "tesseract process timeout" in str(error).lower():
                        logger.info(
                            "Image OCR pass %s timed out; scan result is incomplete",
                            pass_index + 1,
                        )
                    else:
                        logger.warning(
                            "Image OCR pass %s failed (RuntimeError: %s)",
                            pass_index + 1,
                            str(error)[:200],
                        )
                    break
                except Exception as error:
                    complete = False
                    # Do not claim a timeout or bad image means OCR is uninstalled.
                    logger.warning(
                        "Image OCR pass %s failed (%s: %s)",
                        pass_index + 1,
                        type(error).__name__,
                        str(error)[:200],
                    )
                    break
                if found.strip():
                    recognized.append(found.strip())
                combined = re.sub(r"\s+", " ", "\n".join(recognized))
                if self._ocr_has_scam_evidence(combined):
                    break
        text = "\n".join(dict.fromkeys(recognized))[:12000]
        qr_values: list[str] = []
        if cv2 is not None and np is not None:
            try:
                # Reuse the bounded RGB decode, rather than decoding full size again.
                image = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
                detector = cv2.QRCodeDetector()
                decoded, points, _ = detector.detectAndDecode(image)
                if decoded:
                    qr_values.append(decoded.strip())
                if points is not None and not decoded and hasattr(detector, "detectAndDecodeMulti"):
                    found, values, _, _ = detector.detectAndDecodeMulti(image)
                    if found:
                        qr_values.extend(value.strip() for value in values if value.strip())
            except Exception:
                logger.debug("QR image scan failed", exc_info=True)
        logger.debug(
            "Image scan finished in %.3fs; OCR passes=%s chars=%s QR=%s complete=%s",
            time.monotonic() - started, passes, len(text), len(qr_values), complete,
        )
        return text, qr_values, complete

    def _read_image_text_and_qr(self, image_bytes: bytes) -> tuple[str, list[str]]:
        text, qr_values, _ = self._scan_image_worker(image_bytes)
        return text, qr_values

    async def _cached_image_text_and_qr(self, image_bytes: bytes) -> tuple[str, list[str]]:
        key = hashlib.sha256(image_bytes).hexdigest()
        now = time.monotonic()
        cached = self._image_scan_cache.get(key)
        if cached is not None and now - cached[0] < OCR_CACHE_TTL:
            self._image_scan_cache.move_to_end(key)
            return cached[1], list(cached[2])
        self._image_scan_cache.pop(key, None)
        task = self._image_scan_tasks.get(key)
        if task is None:
            async def scan():
                # Slot belongs to the task, so cancellation of a message handler
                # cannot release it while a Tesseract subprocess is still running.
                async with self._image_scan_slots:
                    text, values, complete = await asyncio.to_thread(
                        self._scan_image_worker, image_bytes
                    )
                if complete:
                    self._image_scan_cache[key] = (time.monotonic(), text, tuple(values))
                    self._image_scan_cache.move_to_end(key)
                    while len(self._image_scan_cache) > OCR_CACHE_MAX_ITEMS:
                        self._image_scan_cache.popitem(last=False)
                return text, values
            task = asyncio.create_task(scan())
            self._image_scan_tasks[key] = task
            def finished(done):
                self._image_scan_tasks.pop(key, None)
                if not done.cancelled():
                    done.exception()  # Retrieve errors even if all waiters were cancelled.
            task.add_done_callback(finished)
        text, values = await asyncio.shield(task)
        return text, list(values)

    async def _image_scam_signals(
        self,
        attachment: discord.Attachment,
        guild_id: int | None = None,
        *,
        prepared: tuple[bytes, str] | None = None,
    ) -> list[str]:
        if attachment.size > IMAGE_SCAN_MAX_BYTES:
            logger.debug(
                "Skipping oversized image attachment %s (%s bytes)",
                attachment.id,
                attachment.size,
            )
            return []
        if prepared is None:
            try:
                image_bytes = await attachment.read()
                image_hash = await asyncio.to_thread(self._average_hash, image_bytes)
            except Exception:
                # Decide from the bytes whether this is an image. Names and MIME
                # types are user-controlled and can be misleading or absent.
                logger.debug("Attachment %s is not a scannable image", attachment.id)
                return []
        else:
            image_bytes, image_hash = prepared
        return await self._image_bytes_scam_signals(
            image_bytes, guild_id, image_hash=image_hash
        )

    @staticmethod
    def _is_discord_image_proxy(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "https"
                and parsed.username is None
                and parsed.password is None
                and bool(DISCORD_IMAGE_PROXY_HOST_RE.fullmatch(parsed.hostname or ""))
            )
        except ValueError:
            return False

    @staticmethod
    def _download_discord_image_proxy(url: str) -> bytes | None:
        class SafeRedirectHandler(HTTPRedirectHandler):
            def redirect_request(self, request, file, code, message, headers, new_url):
                if not AntiScam._is_discord_image_proxy(new_url):
                    return None
                return super().redirect_request(
                    request, file, code, message, headers, new_url
                )

        if not AntiScam._is_discord_image_proxy(url):
            return None
        request = Request(url, headers={"User-Agent": "Observer image scanner"})
        opener = build_opener(SafeRedirectHandler)
        try:
            with opener.open(request, timeout=4) as response:
                if not AntiScam._is_discord_image_proxy(response.geturl()):
                    return None
                if not (response.headers.get("Content-Type") or "").lower().startswith("image/"):
                    return None
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > IMAGE_SCAN_MAX_BYTES:
                    return None
                data = response.read(IMAGE_SCAN_MAX_BYTES + 1)
                return data if len(data) <= IMAGE_SCAN_MAX_BYTES else None
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            logger.debug("Could not fetch Discord-proxied image", exc_info=True)
            return None

    async def _embedded_image_scam_signals(
        self, message: discord.Message
    ) -> list[str]:
        proxy_urls: set[str] = set()
        for embed in getattr(message, "embeds", ()):
            for image_part in (embed.image, embed.thumbnail):
                proxy_url = getattr(image_part, "proxy_url", None)
                if proxy_url and self._is_discord_image_proxy(proxy_url):
                    proxy_urls.add(proxy_url)

        signals: list[str] = []
        for proxy_url in proxy_urls:
            image_bytes = await asyncio.to_thread(
                self._download_discord_image_proxy, proxy_url
            )
            if image_bytes is not None:
                signals.extend(
                    await self._image_bytes_scam_signals(image_bytes, message.guild.id)
                )
        return list(dict.fromkeys(signals))

    async def _image_bytes_scam_signals(
        self,
        image_bytes: bytes,
        guild_id: int | None,
        *,
        image_hash: str | None = None,
    ) -> list[str]:
        if image_hash is None:
            try:
                image_hash = await asyncio.to_thread(self._average_hash, image_bytes)
            except Exception:
                logger.debug("Attachment bytes are not a scannable image", exc_info=True)
                return []

        if self._image_is_trained_good(image_hash, image_bytes, guild_id):
            return []

        signals: list[str] = []
        known_hashes = list(self.global_scam_image_hashes)
        if guild_id is not None:
            known_hashes.extend(self.guild_scam_image_hashes.get(str(guild_id), ()))
        matches = [
            item for item in known_hashes
            if self._hash_distance(image_hash, item["hash"]) <= IMAGE_HASH_DISTANCE
        ]
        if matches:
            label = matches[0].get("label") or "known scam image"
            signals.append(f"image resembles confirmed scam: {label}")

        # Only an exact, moderator-trained file match can bypass OCR.
        # Legacy 64-bit average hashes are too coarse for that decision.
        exact_hash = hashlib.sha256(image_bytes).hexdigest()
        if any(item.get("sha256") == exact_hash for item in known_hashes):
            if not signals:
                signals.append("image resembles confirmed scam: exact trained file")
            return signals
        text, qr_values = await self._cached_image_text_and_qr(image_bytes)
        text = re.sub(r"\s+", " ", text).strip()
        signals.extend(image_ocr_text_signals(text))
        for decoded in qr_values:
            if URL_RE.search(decoded):
                signals.append("QR code contains a link")
                signals.extend(f"QR link {signal}" for signal in detect_scam_signals(decoded))
            elif QR_PHRASE_RE.search(text):
                signals.append("QR code contains text beside scam instructions")
        return list(dict.fromkeys(signals))

    async def _train_scam_image(
        self, image: discord.Attachment, label: str, guild_id: int | None
    ) -> bool:
        if image.size > IMAGE_SCAN_MAX_BYTES:
            return False
        try:
            image_bytes = await image.read()
            image_hash = await asyncio.to_thread(self._average_hash, image_bytes)
        except Exception:
            return False
        async with self._scam_hash_lock:
            item = {"hash": image_hash, "label": label[:80],
                    "sha256": hashlib.sha256(image_bytes).hexdigest()}
            if guild_id is None:
                self.global_scam_image_hashes.append(item)
            else:
                self.guild_scam_image_hashes.setdefault(str(guild_id), []).append(item)
            await asyncio.to_thread(self._save_scam_image_hashes)
            logger.info(
                "Saved trained scam image fingerprint (scope=%s, file=%s)",
                "global" if guild_id is None else f"guild:{guild_id}",
                SCAM_IMAGE_HASHES,
            )
        return True

    @app_commands.command(
        name="scam-image-test",
        description="Test image OCR and scam signals without moderation actions.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def scam_image_test(
        self, interaction: discord.Interaction, image: discord.Attachment,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if image.size > IMAGE_SCAN_MAX_BYTES:
            await interaction.followup.send("Image must be 8 MB or smaller.", ephemeral=True)
            return
        started = time.monotonic()
        try:
            data = await image.read()
            image_hash = await asyncio.to_thread(self._average_hash, data)
            text, qr_values = await self._cached_image_text_and_qr(data)
        except Exception as error:
            error_id = log_error(error, source="anti-scam image test", guild_id=interaction.guild_id)
            await interaction.followup.send(
                f"Image test failed. Error ID: {error_id}", ephemeral=True,
            )
            return
        trained_good = self._image_is_trained_good(image_hash, data, interaction.guild_id)
        signals = [] if trained_good else image_ocr_text_signals(text)
        if not trained_good:
            for value in qr_values:
                if URL_RE.search(value):
                    signals.append("QR code contains a link")
                    signals.extend(f"QR link {signal}" for signal in detect_scam_signals(value))
        known = list(self.global_scam_image_hashes)
        known.extend(self.guild_scam_image_hashes.get(str(interaction.guild_id), ()))
        matches = [item for item in known
                   if self._hash_distance(image_hash, item["hash"]) <= IMAGE_HASH_DISTANCE]
        if matches and not trained_good:
            signals.append("image resembles confirmed scam: trained fingerprint")
        signals = list(dict.fromkeys(signals))
        key = hashlib.sha256(data).hexdigest()
        complete = key in self._image_scan_cache
        status = "Extraction completed" if complete else "Extraction incomplete (see OCR warning in logs)"
        if not text.strip():
            status += "; no text recognised"
        details = (
            f"Elapsed: {time.monotonic() - started:.2f}s (includes download/queue/cache)\n"
            f"Status: {status}\n"
            f"Loaded cog: {Path(__file__).resolve()}\n"
            f"Tesseract executable: {pytesseract.pytesseract.tesseract_cmd if pytesseract else '[pytesseract missing]'}\n"
            f"Security module enabled: {self._enabled(interaction.guild_id)}\n"
            f"Moderation log channel: {self.store.get(interaction.guild_id, 'moderation', 'log_channel', None)}\n"
            f"Your configured bypass role applies: {self._has_scam_bypass_role(interaction)}\n"
            f"Signals: {len(signals)}\n"
            + ("Training: moderator-confirmed benign image\n" if trained_good else "")
            + "\n".join(f"- {signal}" for signal in signals)
            + "\n\nThis diagnostic performs no moderation actions. GIFs use the first frame."
            + "\n\nExtracted text:\n" + (text or "[none]")
        )
        # Put URLs/QR data in a text attachment, not clickable embed previews.
        report = discord.File(BytesIO(details.encode("utf-8")), filename="scam-image-test.txt")
        await interaction.followup.send(
            f"Image test: {len(signals)} signal(s). {status}. See the attached report.",
            file=report, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="scam-image-train",
        description="Train image matching for this server.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def scam_image_train(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        label: str = "confirmed scam",
    ) -> None:
        if image.size > IMAGE_SCAN_MAX_BYTES:
            await interaction.response.send_message(
                f"Image must be {IMAGE_SCAN_MAX_BYTES // (1024 * 1024)} MB or smaller.",
                ephemeral=True,
            )
            return
        trained = await self._train_scam_image(image, label, interaction.guild_id)
        if not trained:
            await interaction.response.send_message(
                "I could not read that image file.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Added the image fingerprint for this server. Similar images will be flagged for moderator review.",
            ephemeral=True,
        )

    @app_commands.command(
        name="scam-image-train-global",
        description="Train image matching across every server.",
    )
    @app_commands.check(lambda interaction: is_bot_owner(interaction.user))
    async def scam_image_train_global(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        label: str = "confirmed scam",
    ) -> None:
        if image.size > IMAGE_SCAN_MAX_BYTES:
            await interaction.response.send_message(
                f"Image must be {IMAGE_SCAN_MAX_BYTES // (1024 * 1024)} MB or smaller.",
                ephemeral=True,
            )
            return
        trained = await self._train_scam_image(image, label, guild_id=None)
        if not trained:
            await interaction.response.send_message(
                "I could not read that image file.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Added the image fingerprint globally. Similar images will be flagged across all servers.",
            ephemeral=True,
        )

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

    def _raid_get(self, guild_id: int, key: str, default=None):
        setting_key = RAID_SETTING_KEYS.get(key, key)
        if setting_key != key or key.startswith("honeypot_"):
            value = self.store.get(guild_id, MODULE_KEY, setting_key, None)
            if value is not None:
                return value
        return self.store.get(guild_id, ANTI_RAID_MODULE_KEY, key, default)

    def get(self, guild_id: int, key: str, default=None):
        return self._raid_get(guild_id, key, default)

    def number(self, guild_id: int, key: str, default: int, minimum: int, maximum: int) -> int:
        return self._raid_number(guild_id, key, default, minimum, maximum)

    def _raid_number(self, guild_id: int, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(self._raid_get(guild_id, key, default))))
        except (TypeError, ValueError):
            return default

    def _honeypot_exemption_reason(self, member: discord.Member) -> str | None:
        if member.bot:
            return "bot account"
        if member.id == member.guild.owner_id:
            return "server owner"
        permissions = member.guild_permissions
        for permission in (
            "administrator", "manage_guild", "moderate_members", "ban_members",
            "manage_messages", "kick_members",
        ):
            if getattr(permissions, permission, False):
                return f"permission: {permission}"
        bypass = self._raid_get(member.guild.id, "bypass_role")
        if bypass and any(str(role.id) == str(bypass) for role in member.roles):
            return "configured bypass role"
        return None

    def honeypot_exemption_reason(self, member: discord.Member) -> str | None:
        return self._honeypot_exemption_reason(member)

    def exempt(self, member: discord.Member) -> bool:
        return self._honeypot_exemption_reason(member) is not None

    async def _raid_alert(self, guild: discord.Guild, text: str) -> None:
        channel_id = self._raid_get(guild.id, "alert_channel")
        try:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
        except (TypeError, ValueError):
            channel = None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as error:
                log_error(error, source="anti-scam alert", guild_id=guild.id)

    async def alert(self, guild: discord.Guild, text: str) -> None:
        await self._raid_alert(guild, text)

    async def _restrict_for_raid(
        self, member: discord.Member, reason: str, *, ban: bool = False, minutes: int = 10,
        mute_role: discord.Role | None = None,
    ) -> str:
        me = member.guild.me
        if self._honeypot_exemption_reason(member):
            return "exempt"
        permission = "ban_members" if ban else ("manage_roles" if mute_role else "moderate_members")
        if not me or not getattr(me.guild_permissions, permission) or member.top_role >= me.top_role:
            return f"not applied: missing {permission} or role hierarchy"
        if mute_role is not None and mute_role >= me.top_role:
            return "not applied: mute role must be below the bot's top role"
        try:
            if ban:
                await member.ban(reason=reason, delete_message_seconds=0)
            elif mute_role is not None:
                await member.add_roles(mute_role, reason=reason)
            elif minutes:
                deadline = discord.utils.utcnow() + timedelta(minutes=minutes)
                existing = getattr(member, "timed_out_until", None)
                if existing is not None and existing >= deadline:
                    return "existing longer timeout kept"
                await member.timeout(deadline, reason=reason)
            else:
                return "timeout disabled"
        except discord.HTTPException as error:
            error_id = log_error(error, source="anti-raid enforcement", guild_id=member.guild.id, user_id=member.id)
            return f"failed; error {error_id}"
        action_type = "ban" if ban else ("mute" if mute_role else "timeout")
        expires_at = (
            (discord.utils.utcnow() + timedelta(minutes=minutes)).isoformat()
            if action_type == "timeout" and minutes else None
        )
        action_id = self.actions.create(
            member.guild.id, action_type, member.id, self.bot.user.id
        )
        case_id = self.cases.create_case(
            member.guild.id, action_type, member.id,
            self.bot.user.id, reason, status="active", action_id=action_id,
            expires_at=expires_at,
        )
        self.last_case_ids[(member.guild.id, member.id)] = case_id
        self.last_action_ids[(member.guild.id, member.id)] = action_id
        try:
            appeal_view = None
            if ban:
                moderation = self.bot.get_cog("ModerationCog")
                if moderation is not None:
                    # Reuse the same persistent appeal control as manual bans.
                    # No moderator is attached for automated enforcement.
                    from cogs.moderation import PermanentBanDMView

                    appeal_view = PermanentBanDMView(moderation)
            dm_message = await dm_moderation_action(
                member,
                member.guild,
                "Ban" if ban else ("Permanent mute" if mute_role else f"Timeout for {minutes} minute(s)"),
                reason,
                view=appeal_view,
            )
            if ban and dm_message is not None and appeal_view is not None:
                self.cases.set_appeal_message(case_id, dm_message.id)
        except Exception:
            # A notification problem must not undo or interrupt enforcement.
            logger.exception("Could not notify anti-raid target %s", member.id)
        return "banned" if ban else (f"permanently muted with {mute_role.name}" if mute_role else f"timed out for {minutes} minutes")

    async def _handle_raid_join(self, member: discord.Member) -> None:
        guild = member.guild
        if not self._raid_get(guild.id, "enabled", False) or self._honeypot_exemption_reason(member):
            return
        async with self.raid_locks[guild.id]:
            now = time.time()
            joins = self.joins[guild.id]
            window = self._raid_number(guild.id, "join_window_seconds", 30, 5, 3600)
            while joins and now - joins[0][0] > window:
                joins.popleft()
            joins.append((now, member.id))
            threshold = self._raid_number(guild.id, "join_threshold", 10, 2, 1000)
            active = float(self._raid_get(guild.id, "hold_until", 0) or 0) > now
            triggered = not active and len(joins) >= threshold
            if triggered:
                hold = self._raid_number(guild.id, "hold_seconds", 300, 10, 86400)
                self.store.set(guild.id, ANTI_RAID_MODULE_KEY, "hold_until", now + hold)
            if not active and not triggered:
                return
            ids = [uid for _, uid in joins] if triggered else [member.id]
            minutes = self._raid_number(guild.id, "timeout_minutes", 10, 0, 40320)
            results = []
            for uid in ids:
                target = guild.get_member(uid)
                if target:
                    result = await self.restrict(
                        target, "Automatic join-burst anti-raid restriction", minutes=minutes
                    )
                    case_id = self.last_case_ids.get((guild.id, uid))
                    results.append(f"{uid}: {result}" + (f" (case {case_id})" if case_id else ""))
            await self.alert(
                guild,
                "**Anti-raid verification hold active**\n"
                + (f"Detected {len(joins)} joins in {window}s.\n" if triggered else "New arrival during hold.\n")
                + "\n".join(results),
            )

    async def _handle_honeypot(self, message: discord.Message) -> None:
        if message.guild is None or getattr(message, "webhook_id", None) or not isinstance(message.author, discord.Member):
            return
        guild = message.guild
        if (
            not self._raid_get(guild.id, "honeypot_enabled", False)
            or str(message.channel.id) != str(self._raid_get(guild.id, "honeypot_channel"))
        ):
            return
        exemption = self._honeypot_exemption_reason(message.author)
        if exemption:
            logger.info(
                "Honeypot exempt guild=%s channel=%s user=%s reason=%s",
                guild.id, message.channel.id, message.author.id, exemption,
            )
            return
        key = (guild.id, message.author.id)
        if key in self.trapped:
            return
        self.trapped.add(key)
        try:
            reason = "Posted in the configured do-not-post honeypot channel"
            mute_role_id = self._get_role_id(guild.id, "honeypot_mute_role")
            mute_role = guild.get_role(mute_role_id) if mute_role_id else None
            permanent_mute = self._bool_setting(guild.id, "honeypot_permanent_mute", False)
            if permanent_mute and mute_role is None:
                result = "not applied: configure the honeypot permanent mute role"
            else:
                result = await self.restrict(
                    message.author, reason,
                    ban=bool(self._raid_get(guild.id, "honeypot_ban", False)),
                    minutes=60 if not permanent_mute else 0,
                    mute_role=mute_role if permanent_mute else None,
                )
            if result == "banned":
                get_cog = getattr(self.bot, "get_cog", None)
                moderation = get_cog("ModerationCog") if get_cog else None
                if moderation is not None and guild.me is not None:
                    await moderation._send_mod_log(
                        guild=guild, action="Ban", moderator=guild.me,
                        target=message.author, reason=reason,
                        case_id=self.last_case_ids.get(key),
                        action_id=self.last_action_ids.get(key),
                    )
            try:
                await message.delete()
            except discord.HTTPException as error:
                log_error(error, source="honeypot delete", guild_id=guild.id, user_id=message.author.id)
            await self.alert(
                guild,
                f"**Honeypot triggered**\nMember: {message.author.id}\n"
                f"Channel: {message.channel.id}\nAction: {result}",
            )
        finally:
            self.trapped.discard(key)

    async def restrict(
        self, member: discord.Member, reason: str, *, ban: bool = False, minutes: int = 10,
        mute_role: discord.Role | None = None,
    ) -> str:
        return await self._restrict_for_raid(member, reason, ban=ban, minutes=minutes, mute_role=mute_role)

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
            role.id == role_id for role in getattr(
                getattr(message, "author", None) or getattr(message, "user", None), "roles", ()
            )
        )

    async def _check_cross_server_scam_history(
        self, message: discord.Message
    ) -> bool:
        """Apply this server's opt-in policy to confirmed bans and accepted reports elsewhere."""
        guild = message.guild
        member = message.author
        if guild is None or not isinstance(member, discord.Member):
            return False

        key = (guild.id, member.id)
        now = time.monotonic()
        if self.cross_server_enforcements.get(key, 0) > now:
            return False
        if self.cross_server_check_cache.get(key, 0) > now:
            return False

        confirmed_enabled = self._bool_setting(
            guild.id, "cross_server_scam_protection", False
        )
        reports_enabled = self._bool_setting(
            guild.id, "cross_server_report_timeout", False
        )
        if not confirmed_enabled and not reports_enabled:
            return False

        reason = None
        title = None
        enforcement_cache_seconds = 600
        if confirmed_enabled:
            source = await asyncio.to_thread(
                self.cases.find_cross_server_scam_ban,
                member.id,
                exclude_guild_id=guild.id,
            )
            if source is not None:
                source_guild_id = int(source["guild_id"])
                reason = (
                    "Confirmed high-confidence scam ban in another Observer server "
                    f"(source server {source_guild_id})"
                )
                title = "Confirmed cross-server scam account restricted"
                ban = self._bool_setting(guild.id, "cross_server_scam_ban", False)
                timeout_minutes = self._int_setting(
                    guild.id, "cross_server_scam_timeout_minutes", 1440, 1, 40320
                )
                if not ban:
                    enforcement_cache_seconds = max(600, timeout_minutes * 60)
                result = await self.restrict(
                    member, reason, ban=ban, minutes=timeout_minutes
                )

        if reason is None and reports_enabled:
            threshold = self._int_setting(
                guild.id, "cross_server_report_threshold", 2, 1, 100
            )
            since = int(time.time()) - 30 * 24 * 60 * 60
            report_guilds = await asyncio.to_thread(
                self.report_store.accepted_guild_count,
                member.id,
                exclude_guild_id=guild.id,
                since=since,
            )
            if report_guilds >= threshold:
                timeout_minutes = self._int_setting(
                    guild.id, "cross_server_report_timeout_minutes", 60, 1, 40320
                )
                enforcement_cache_seconds = max(600, timeout_minutes * 60)
                reason = (
                    f"Staff-accepted reports in {report_guilds} other Observer servers "
                    "within the last 30 days"
                )
                title = "Cross-server report threshold reached"
                result = await self.restrict(
                    member, reason, minutes=timeout_minutes
                )

        if reason is None:
            self.cross_server_check_cache[key] = now + 30
            return False

        if "failed" not in result and "not applied" not in result:
            self.cross_server_enforcements[key] = now + enforcement_cache_seconds
        await self._alert(
            guild,
            title or "Cross-server account check",
            f"Member: {member.mention} (`{member.id}`)\n{reason}\nAction: {result}",
        )
        return True

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

    async def _alert(
        self,
        guild: discord.Guild,
        title: str,
        description: str,
        *,
        ping_role: discord.Role | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
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
                content=ping_role.mention if ping_role is not None else None,
                embed=discord.Embed(title=title, description=description, color=0xED4245),
                view=view,
                allowed_mentions=(
                    discord.AllowedMentions(
                        everyone=False,
                        users=False,
                        roles=[ping_role],
                        replied_user=False,
                    )
                    if ping_role is not None
                    else discord.AllowedMentions.none()
                ),
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
        await self._handle_raid_join(member)
        if not self._enabled(member.guild.id):
            return
        reasons = self._risk_reasons(member)
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
                    reason = "Temporary lockdown new-account restriction"
                    await member.timeout(
                        timedelta(minutes=timeout_minutes),
                        reason=reason,
                    )
                    await dm_moderation_action(
                        member,
                        member.guild,
                        f"Timeout for {timeout_minutes} minute(s)",
                        reason,
                    )
                    action_id = self.actions.create(
                        member.guild.id,
                        "timeout",
                        member.id,
                        self.bot.user.id,
                    )
                    case_id = self.cases.create_case(
                        member.guild.id,
                        "timeout",
                        member.id,
                        self.bot.user.id if self.bot.user else None,
                        reason,
                        status="active",
                        action_id=action_id,
                        expires_at=(
                            discord.utils.utcnow()
                            + timedelta(minutes=timeout_minutes)
                        ).isoformat(),
                    )
                    reasons.append(
                        f"lockdown restriction applied (case {case_id})"
                    )
                else:
                    reasons.append("lockdown restriction configured to zero minutes")
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
        await self._handle_honeypot(message)
        if message.guild is None or (
            self.bot.user is not None and message.author.id == self.bot.user.id
        ):
            return
        if not self._enabled(message.guild.id):
            return
        if self._has_scam_bypass_role(message):
            return
        if isinstance(message.author, discord.Member):
            if await self._check_cross_server_scam_history(message):
                return
        key = (message.guild.id, message.author.id)
        received_at = time.monotonic()
        activity = self.recent_user_activity[key]
        async with self.activity_load_locks[key]:
            if key not in self.activity_history_loaded:
                try:
                    wall_now = time.time()
                    stored = await asyncio.to_thread(
                        self.activity_store.load,
                        message.guild.id,
                        message.author.id,
                        wall_now - self.activity_store.RETENTION_SECONDS,
                    )
                    for event in reversed(stored):
                        age = max(0.0, wall_now - event["created_at"])
                        activity.append((
                            received_at - age,
                            event["channel_id"],
                            bool(event["has_image"]),
                            bool(event["normal_text"]),
                            event["message_id"],
                        ))
                except Exception:
                    logger.warning(
                        "Could not restore recent anti-scam activity for guild %s user %s",
                        message.guild.id,
                        message.author.id,
                        exc_info=True,
                    )
                self.activity_history_loaded.add(key)
        while activity:
            oldest = activity[0]
            if (
                not isinstance(oldest, (tuple, list))
                or not oldest
                or not isinstance(oldest[0], (int, float))
            ):
                activity.popleft()
                continue
            if received_at - oldest[0] <= 600:
                break
            activity.popleft()
        raw_content = getattr(message, "content", "")
        content = raw_content.strip() if isinstance(raw_content, str) else ""
        attachments = getattr(message, "attachments", ()) or ()
        embeds = getattr(message, "embeds", ()) or ()
        has_image_attachment = any(
            is_image_attachment(item) for item in attachments
        ) or any(
            getattr(embed, "image", None) or getattr(embed, "thumbnail", None)
            for embed in embeds
        )
        normal_text = (
            len(content) >= 20
            and not URL_RE.search(content)
            and not detect_scam_signals(content)
        )
        if has_image_attachment or normal_text:
            activity.append((
                received_at,
                getattr(message.channel, "id", None),
                has_image_attachment,
                normal_text,
                message.id,
            ))
            try:
                await asyncio.to_thread(
                    self.activity_store.record,
                    message.guild.id,
                    message.author.id,
                    getattr(message.channel, "id", None),
                    message.id,
                    time.time(),
                    has_image_attachment,
                    normal_text,
                )
            except Exception:
                logger.warning(
                    "Could not persist recent anti-scam activity for guild %s user %s",
                    message.guild.id,
                    message.author.id,
                    exc_info=True,
                )
        quarantined_until = self.scam_quarantine.get(key, 0)
        if quarantined_until > received_at and has_image_attachment:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logger.warning(
                    "Could not remove quarantined message %s in guild %s",
                    message.id,
                    message.guild.id,
                )
            return
        if has_image_attachment:
            recent = self.recent_image_messages[key]
            while recent and received_at - recent[0][0] > 60:
                recent.popleft()
            recent.append((received_at, message))

        prepared_images = await self._prepare_image_fingerprints(attachments)
        repeated_matches = []
        fingerprints = deque(
            (
                event for event in self.recent_image_fingerprints[key]
                if isinstance(event, (tuple, list))
                and len(event) == 6
                and isinstance(event[0], (int, float))
                and abs(received_at - event[0]) <= 60
            ),
            maxlen=20,
        )
        self.recent_image_fingerprints[key] = fingerprints
        for attachment_id, image_bytes, image_hash, exact_hash in prepared_images:
            for event in fingerprints:
                created, channel_id, old_hash, old_exact_hash, old_size, old_message = event
                if channel_id == message.channel.id or abs(received_at - created) > 2:
                    continue
                near_same_size = min(len(image_bytes), old_size) >= 0.8 * max(
                    len(image_bytes), old_size
                )
                same_image = exact_hash == old_exact_hash or (
                    near_same_size and self._hash_distance(image_hash, old_hash) <= 2
                )
                if same_image:
                    repeated_matches.append(old_message)
            fingerprints.append((
                received_at, message.channel.id, image_hash, exact_hash,
                len(image_bytes), message,
            ))
        if repeated_matches:
            await self._handle_cross_channel_image_repost(
                message, key, repeated_matches
            )
            self.recent_image_messages[key].clear()
            self.recent_image_fingerprints[key].clear()
            return
        # Serialize each sender's messages. If one image takes longer to OCR
        # and proves to be a scam, later posts queued from other channels are
        # deleted without waiting for OCR on every one.
        async with self.scam_user_locks[key]:
            until = self.scam_quarantine.get(key, 0)
            if until > time.monotonic() and has_image_attachment:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    logger.warning("Could not remove quarantined message %s in guild %s", message.id, message.guild.id)
                return
            if until and until <= time.monotonic():
                self.scam_quarantine.pop(key, None)
            await self._process_scam_message(
                message,
                key,
                prepared_images={
                    attachment_id: (image_bytes, image_hash)
                    for attachment_id, image_bytes, image_hash, _ in prepared_images
                },
            )

    async def _prepare_image_fingerprints(self, attachments):
        """Read image bytes once and compute cheap hashes before the slow OCR path."""
        image_attachments = tuple(
            attachment for attachment in attachments
            if is_image_attachment(attachment)
            and isinstance(getattr(attachment, "size", None), int)
            and attachment.size <= IMAGE_SCAN_MAX_BYTES
        )

        async def prepare(attachment):
            try:
                image_bytes = await attachment.read()
                image_hash = await asyncio.to_thread(self._average_hash, image_bytes)
                exact_hash = hashlib.sha256(image_bytes).hexdigest()
                return attachment.id, image_bytes, image_hash, exact_hash
            except Exception:
                logger.debug("Could not fingerprint attachment %s", attachment.id, exc_info=True)
                return None

        prepared = await asyncio.gather(*(prepare(item) for item in image_attachments))
        return tuple(item for item in prepared if item is not None)

    async def _handle_cross_channel_image_repost(
        self,
        message: discord.Message,
        key: tuple[int, int],
        previous_messages: list[discord.Message],
    ) -> None:
        now = time.monotonic()
        self.scam_quarantine[key] = now + SCAM_SENDER_QUARANTINE_SECONDS
        self.scam_alerts[key] = now
        self.scam_enforcements[key] = now
        self.recent_image_messages[key].clear()
        self.recent_image_fingerprints[key].clear()
        account_age_days = max(
            0,
            (discord.utils.utcnow() - getattr(
                message.author, "created_at", discord.utils.utcnow()
            )).days,
        )
        recent_normal_post = any(
            isinstance(event, (tuple, list))
            and len(event) >= 5
            and isinstance(event[0], (int, float))
            and now - event[0] <= 600
            and event[3]
            and event[4] != message.id
            for event in self.recent_user_activity[key]
        )
        joined_at = getattr(message.author, "joined_at", None)
        joined_recently = bool(
            joined_at is not None
            and discord.utils.utcnow() - joined_at <= timedelta(hours=24)
        )
        account_context = (
            f"Account age: {account_age_days} day(s); "
            f"joined this server within 24 hours: {'yes' if joined_recently else 'no'}; "
            f"recent normal posts observed: {'yes' if recent_normal_post else 'no'}"
        )
        self.cases.create_case(
            message.guild.id,
            "scam_flag",
            message.author.id,
            reason=(
                "Same or near-identical image reposted across channels within 2 seconds; "
                + account_context
            ),
            source_message_id=message.id,
            status="open",
        )
        candidates = {candidate.id: candidate for candidate in previous_messages}
        candidates[message.id] = message

        async def remove(candidate):
            try:
                await candidate.delete(reason="Rapid cross-channel image repost")
            except TypeError:
                await candidate.delete()
            except discord.NotFound:
                return

        deletion_future = None
        if self._bool_setting(message.guild.id, "delete_scam_messages", True):
            deletion_future = asyncio.gather(
                *(remove(candidate) for candidate in candidates.values()),
                return_exceptions=True,
            )
        action_task = asyncio.create_task(self.restrict(
            message.author,
            "Same or near-identical image reposted across channels within 2 seconds",
            minutes=SCAM_UNCERTAIN_TIMEOUT_MINUTES,
        ))
        if deletion_future is not None:
            results = await deletion_future
            errors = [result for result in results if isinstance(result, Exception)]
            removed_count = len(results) - len(errors)
            if errors:
                logger.warning(
                    "Rapid repost cleanup had %s failure(s) in guild %s (types: %s)",
                    len(errors), message.guild.id,
                    ", ".join(sorted({type(error).__name__ for error in errors})),
                )
        else:
            removed_count = 0
        action = await action_task
        moderator_role = None
        try:
            role_id = int(self.store.get(message.guild.id, "moderation", "mod_role"))
            moderator_role = message.guild.get_role(role_id)
            if moderator_role is not None and moderator_role.is_default():
                moderator_role = None
        except (TypeError, ValueError):
            pass
        await self._alert(
            message.guild,
            "Rapid cross-channel image repost",
            f"Author: {message.author.mention}\nChannel: <#{message.channel.id}>\n"
            f"Matched image posts across {len(candidates)} message(s) within 2 seconds. "
            f"Removed: {removed_count}.\n{account_context}\n"
            f"Moderation: {action}",
            ping_role=moderator_role,
        )

    async def _process_scam_message(
        self,
        message: discord.Message,
        alert_key: tuple[int, int],
        *,
        prepared_images: dict[int, tuple[bytes, str]] | None = None,
    ) -> None:
        prepared_images = prepared_images or {}
        signals = detect_scam_signals(message.content)
        signals.extend(detect_crypto_giveaway_signals(message.content))
        signals.extend(detect_nitro_gift_image_signals(message.content))
        embed_text = message_embed_text(message)
        signals.extend(detect_scam_signals(embed_text))
        signals.extend(detect_scam_giveaway_signals(embed_text))
        signals.extend(detect_crypto_giveaway_signals(embed_text))
        signals.extend(detect_nitro_gift_image_signals(embed_text))
        attachments = tuple(getattr(message, "attachments", ()))
        attachment_results = await asyncio.gather(
            *(
                self._image_scam_signals(
                    attachment,
                    message.guild.id,
                    prepared=prepared_images.get(attachment.id),
                )
                for attachment in attachments
            ),
            return_exceptions=True,
        )
        benign_training_hashes = []
        for attachment, result in zip(attachments, attachment_results):
            if isinstance(result, Exception):
                logger.debug("Could not scan attachment %s: %s", attachment.id, result)
                continue
            signals.extend(result)
            prepared = prepared_images.get(attachment.id)
            if prepared is not None:
                image_bytes, image_hash = prepared
                benign_training_hashes.append({
                    "hash": image_hash,
                    "label": "moderator-confirmed benign image",
                    "sha256": hashlib.sha256(image_bytes).hexdigest(),
                })
        signals.extend(await self._embedded_image_scam_signals(message))
        signals = list(dict.fromkeys(signals))
        # Message behavior adds context to image scanning. These are review
        # signals, not proof on their own: a burst across channels or repeated
        # image posts are stronger than a single casual phrase like "bro".
        recent_activity = self.recent_user_activity[alert_key]
        behavior_now = time.monotonic()
        recent_images = [
            event for event in recent_activity
            if event[2] and behavior_now - event[0] <= 60
        ]
        normal_activity_seen = any(
            len(event) >= 5
            and event[3]
            and event[4] != message.id
            and behavior_now - event[0] <= 600
            for event in recent_activity
        )
        cross_channel_burst = (
            len(recent_images) >= 2
            and len({event[1] for event in recent_images}) >= 2
        )
        image_attachments = tuple(
            attachment for attachment in attachments if is_image_attachment(attachment)
        )
        multiple_image_attachments = len(image_attachments) >= 2
        image_like = bool(image_attachments) or any(
            getattr(embed, "image", None) or getattr(embed, "thumbnail", None)
            for embed in getattr(message, "embeds", ())
        )
        says_bro_with_image = bool(
            image_like and re.search(r"\bbro\b", message.content or "", re.IGNORECASE)
        )
        account = message.author
        account_age_days = max(
            0,
            (discord.utils.utcnow() - getattr(account, "created_at", discord.utils.utcnow())).days,
        )
        joined_at = getattr(account, "joined_at", None)
        joined_recently = bool(
            joined_at is not None
            and discord.utils.utcnow() - joined_at <= timedelta(hours=24)
        )
        new_or_newish = account_age_days <= 30 or joined_recently
        weak_image_text = any(
            signal.startswith(("scam phrase in image:", "image text:"))
            for signal in signals
        )
        behavioral_image_risk = bool(
            image_like and (
                cross_channel_burst
                or multiple_image_attachments
                or (
                    says_bro_with_image
                    and (cross_channel_burst or new_or_newish or not normal_activity_seen)
                )
                or (weak_image_text and new_or_newish and not normal_activity_seen)
            )
        )
        if cross_channel_burst:
            signals.append(
                f"rapid image burst across {len({event[1] for event in recent_images})} channels"
            )
        if multiple_image_attachments:
            signals.append("multiple images in one message")
        if says_bro_with_image and (
            cross_channel_burst or new_or_newish or not normal_activity_seen
        ):
            signals.append("casual 'bro' wording with image")
        if behavioral_image_risk and image_like and new_or_newish and not normal_activity_seen:
            age_note = (
                f"account is {account_age_days} day(s) old"
                if account_age_days <= 30
                else "account joined this server within 24 hours"
            )
            signals.append(f"new or newish account with no recent normal posts ({age_note})")
        if behavioral_image_risk:
            signals = list(dict.fromkeys(signals))
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
                "QR-code phishing bait", "suspicious Nitro giveaway embed:",
                "celebrity crypto giveaway scam pattern",
            )) for signal in signals
        )
        contextual_signal = any(signal in SCAM_PHRASES for signal in signals) and any(
            signal.startswith(("shortened link:", "suspicious download link:", "raw IP address link:", "suspicious authorization link:"))
            for signal in signals
        )
        # Automatic punishment is limited to high-confidence indicators. Generic
        # scam phrases and weak URL signals continue to alert without punishment.
        base_image_signal = any(signal.startswith((
            "scam phrase in image:", "image resembles confirmed scam:",
            "MrBeast-style image giveaway bait", "QR-code phishing bait",
            "suspicious Nitro giveaway embed:",
            "celebrity crypto giveaway scam pattern",
            "suspicious Nitro gift claim image",
            "image text:",
        )) for signal in signals)
        confirmed_image_signal = base_image_signal and not any(
            signal.startswith(("scam phrase in image:", "image text:"))
            for signal in signals
        )
        image_signal = base_image_signal or behavioral_image_risk
        uncertain_image_signal = (
            (
                any(signal.startswith(("scam phrase in image:", "image text:")) for signal in signals)
                or behavioral_image_risk
            )
            and not (strong_signal or contextual_signal or confirmed_image_signal)
        )
        delete_enabled = self._bool_setting(
            message.guild.id, "delete_scam_messages", True
        )
        if delete_enabled and image_signal:
            # Keep image posts from this sender across channels for a short
            # window so a multi-message scam burst can be removed together.
            self.scam_quarantine[alert_key] = now + SCAM_SENDER_QUARANTINE_SECONDS
            recent = self.recent_image_messages[alert_key]
            candidates = [
                candidate for created, candidate in recent
                if now - created <= 60
            ]
            if not any(candidate.id == message.id for candidate in candidates):
                candidates.append(message)

            async def delete_candidate(candidate):
                try:
                    await candidate.delete(
                        reason="High-confidence scam image detected"
                    )
                except TypeError:
                    # Some message-like implementations (for example, cached
                    # wrappers) do not accept Discord's optional audit reason.
                    await candidate.delete()

            results = await asyncio.gather(
                *(delete_candidate(candidate) for candidate in candidates),
                return_exceptions=True,
            )
            deleted_count = sum(result is None for result in results)
            deletion_errors = [
                result for result in results
                if isinstance(result, Exception)
                and not isinstance(result, discord.NotFound)
            ]
            failed_count = len(deletion_errors)
            permission_failures = sum(
                isinstance(result, discord.Forbidden)
                for result in deletion_errors
            )
            if failed_count:
                if permission_failures:
                    deletion_result = (
                        f"Could not delete {failed_count} scam image message(s); "
                        "bot needs Manage Messages"
                    )
                else:
                    deletion_result = (
                        f"Could not delete {failed_count} scam image message(s); "
                        "check the bot logs"
                    )
                logger.warning(
                    "Could not remove %s scam messages for user %s in guild %s "
                    "(error types: %s)",
                    failed_count,
                    message.author.id,
                    message.guild.id,
                    ", ".join(sorted({type(error).__name__ for error in deletion_errors})),
                )
            else:
                deletion_result = f"Deleted {deleted_count} recent image message(s)"
            recent.clear()
        elif delete_enabled and (strong_signal or contextual_signal):
            try:
                await message.delete()
            except discord.Forbidden:
                deletion_result = "Deletion failed: bot needs Manage Messages"
            except discord.NotFound:
                deletion_result = "Message was already deleted"
            except discord.HTTPException as error:
                deletion_result = f"Deletion failed: Discord error {error.status}"
            else:
                deletion_result = "Message deleted"

        scam_action_result = "No automatic punishment configured"
        enforce_due = not cooldown or now - self.scam_enforcements.get(
            alert_key, float("-inf")
        ) >= cooldown
        if enforce_due and uncertain_image_signal:
            self.scam_enforcements[alert_key] = now
            scam_action_result = await self.restrict(
                message.author,
                "Uncertain scam image detected; moderator review required",
                minutes=SCAM_UNCERTAIN_TIMEOUT_MINUTES,
            )
        elif enforce_due and (strong_signal or contextual_signal or image_signal):
            scam_ban = self._bool_setting(message.guild.id, "scam_ban", False)
            scam_mute = self._bool_setting(message.guild.id, "scam_permanent_mute", False)
            if scam_ban or scam_mute:
                self.scam_enforcements[alert_key] = now
                mute_role_id = self._get_role_id(message.guild.id, "scam_mute_role")
                mute_role = message.guild.get_role(mute_role_id) if mute_role_id else None
                if scam_mute and mute_role is None:
                    scam_action_result = "Not applied: configure the scam permanent mute role"
                else:
                    scam_action_result = await self.restrict(
                        message.author,
                        "High-confidence scam detection: " + "; ".join(signals),
                        ban=scam_ban,
                        mute_role=mute_role if scam_mute else None,
                    )
        if not alert_due:
            return
        moderator_role = None
        if uncertain_image_signal:
            try:
                moderator_role_id = int(
                    self.store.get(message.guild.id, "moderation", "mod_role")
                )
                moderator_role = message.guild.get_role(moderator_role_id)
                if moderator_role is not None and moderator_role.is_default():
                    moderator_role = None
            except (TypeError, ValueError):
                moderator_role = None
        await self._alert(
            message.guild,
            "Possible scam message",
            f"Author: {message.author.mention}\nChannel: {message.channel.mention}\n"
            f"[Jump to message]({message.jump_url})\nSignals: {', '.join(signals)}\n"
            f"Recent normal text activity: {'seen' if normal_activity_seen else 'not seen'}\n"
            f"Message handling: {deletion_result}\nModeration: {scam_action_result}",
            ping_role=moderator_role,
            view=(NotScamImageView(self, benign_training_hashes)
                  if image_signal and benign_training_hashes else None),
        )

    @app_commands.command(
        name="raid-status",
        description="Show raid hold and honeypot status, or release the hold.",
    )
    @app_commands.guild_only()
    @owner_or_has_guild_permissions(manage_guild=True)
    async def raid_status(self, interaction: discord.Interaction, release: bool = False) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if release:
            await interaction.response.defer(ephemeral=True)
            async with self.raid_locks[guild.id]:
                self.store.set(guild.id, ANTI_RAID_MODULE_KEY, "hold_until", 0)
                self.joins.pop(guild.id, None)
        remaining = max(
            0, int(float(self._raid_get(guild.id, "hold_until", 0) or 0) - time.time())
        )
        message = (
            f"Verification hold: {remaining}s remaining.\n"
            f"Automatic anti-raid: {bool(self._raid_get(guild.id, 'enabled', False))}.\n"
            f"Honeypot: {bool(self._raid_get(guild.id, 'honeypot_enabled', False))}.\n"
            "Configure protection in /setup."
        )
        if release:
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

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


    @app_commands.command(name="lockdown", description="Enable or disable temporary security lockdown.")
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
                await self._alert(guild, "Security lockdown enabled", f"Enabled by {interaction.user.mention}.")
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
                        await self._set_send_permission(channel, item.get("send_messages"), "Security lockdown disabled")
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
    await bot.add_cog(AntiScam(bot))

