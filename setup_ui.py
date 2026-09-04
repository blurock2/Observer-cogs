from __future__ import annotations

import os
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

# Import owner logic from bot.py
from bot import BOT_OWNER_ID, is_bot_owner


def owner_or_has_permissions(**perms: bool):
    """
    Drop-in replacement for ``@app_commands.checks.has_permissions``
    that always lets the bot owner through. Non-owners must still have
    every listed permission, exactly like the built-in check.
    """
    invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
    if invalid:
        raise TypeError(f"Invalid permission(s): {', '.join(sorted(invalid))}")

    async def predicate(interaction: discord.Interaction) -> bool:
        # Owner bypasses the permission requirement entirely.
        if is_bot_owner(interaction.user):
            return True

        permissions = interaction.permissions
        missing = [
            perm
            for perm, value in perms.items()
            if getattr(permissions, perm) != value
        ]
        if not missing:
            return True

        raise app_commands.MissingPermissions(missing)

    return app_commands.check(predicate)


def owner_or_has_guild_permissions(**perms: bool):
    """
    Prefix-command equivalent of :func:`owner_or_has_permissions`.
    Drop-in replacement for ``@commands.has_guild_permissions`` that
    always lets the bot owner through.
    """
    invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
    if invalid:
        raise TypeError(f"Invalid permission(s): {', '.join(sorted(invalid))}")

    async def predicate(ctx: commands.Context) -> bool:
        if is_bot_owner(ctx.author):
            return True

        guild = ctx.guild
        me = guild.me if guild is not None else None

        if guild is None or me is None:
            return False

        permissions = ctx.author.guild_permissions
        missing = [
            perm
            for perm, value in perms.items()
            if getattr(permissions, perm) != value
        ]
        if not missing:
            return True

        raise commands.MissingPermissions(missing)

    return commands.check(predicate)


# ============================================================ Configuration

# Stable, project-root-relative path so the config DB is always in
# <project>/data/bot.db regardless of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(PROJECT_ROOT / "data" / "bot.db")

# Dashboard embed color (#96EDF1).
EMBED_COLOR = 0x96EDF1

# Message Quoter constants
MESSAGE_QUOTER_MODULE_KEY = "message_quoter"
MESSAGE_QUOTER_ROLE_PREFIX = "allowed_role_"
MAX_MESSAGE_QUOTER_ROLES = 10


# ============================================================ SQLite config store

class SetupConfigStore:
    """
    Thin wrapper around a SQLite table that stores per-guild, per-module
    settings as JSON-encoded values.

    Table: guild_setup_config(guild_id, module, key, value)

    Public API
    -----------
    get(guild_id, module, key, default=None) -> any
        Return a single setting value (or `default`).
    set(guild_id, module, key, value) -> None
        Store a setting value.
    get_module(guild_id, module) -> dict[str, any]
        Return all settings for a module as a dict.
    is_enabled(guild_id, module) -> bool
        Convenience: read the "enabled" toggle for a module.
    enable(guild_id, module) / disable(guild_id, module) -> None
        Convenience: write the "enabled" toggle.
    reset_module(guild_id, module) -> None
        Delete every setting for a module.
    reset_guild(guild_id) -> None
        Delete every setting for a guild.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        # `check_same_thread=False` because discord.py calls view
        # callbacks from its own threads.
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_setup_config (
                    guild_id  INTEGER NOT NULL,
                    module    TEXT    NOT NULL,
                    key       TEXT    NOT NULL,
                    value     TEXT    NOT NULL,
                    PRIMARY KEY (guild_id, module, key)
                )
                """
            )

    # ============================================================ Core read / write

    def get(self, guild_id: int, module: str, key: str, default=None):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM guild_setup_config "
                "WHERE guild_id = ? AND module = ? AND key = ?",
                (guild_id, module, key),
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def set(self, guild_id: int, module: str, key: str, value) -> None:
        encoded = json.dumps(value)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_setup_config (guild_id, module, key, value)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, module, key)
                DO UPDATE SET value = excluded.value
                """,
                (guild_id, module, key, encoded),
            )

    def get_module(self, guild_id: int, module: str) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM guild_setup_config "
                "WHERE guild_id = ? AND module = ?",
                (guild_id, module),
            ).fetchall()
        out = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # ============================================================ Enabled toggle helpers

    def is_enabled(self, guild_id: int, module: str) -> bool:
        return bool(self.get(guild_id, module, "enabled", default=False))

    def enable(self, guild_id: int, module: str) -> None:
        self.set(guild_id, module, "enabled", True)

    def disable(self, guild_id: int, module: str) -> None:
        self.set(guild_id, module, "enabled", False)

    def reset_module(self, guild_id: int, module: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM guild_setup_config "
                "WHERE guild_id = ? AND module = ?",
                (guild_id, module),
            )

    def reset_guild(self, guild_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM guild_setup_config WHERE guild_id = ?",
                (guild_id,),
            )


# ============================================================ Setting / module specs

# Allowed setting kinds:
#   "toggle"   -> on/off button
#   "text"     -> free-text modal
#   "integer"   -> numeric modal
#   "channel"   -> channel select
#   "role"      -> role select
SETTING_KINDS = {"toggle", "text", "integer", "channel", "role"}


@dataclass
class SettingSpec:
    """One configurable option inside a module."""

    key: str                 # stored key, e.g. "level_up_channel"
    label: str               # button label, e.g. "Level-up channel"
    kind: str                # one of SETTING_KINDS
    description: str = ""    # shown under the value in the panel
    default: object = None  # default value when never configured


@dataclass
class ModuleSpec:
    """One cog/feature shown in the dashboard dropdown."""

    key: str                          # e.g. "leveling"
    label: str                         # e.g. "Leveling"
    emoji: str                         # e.g. "📈"
    description: str                   # one-line summary
    settings: List[SettingSpec] = field(default_factory=list)


MODULES: List[ModuleSpec] = [
    ModuleSpec(
        key="bot",
        label="Bot System",
        emoji="🤖",
        description="Core bot behavior and logging.",
        settings=[
            SettingSpec(
                "log_channel",
                "Bot log channel",
                "channel",
                description="Channel where the bot posts restart/maintenance notices.",
            ),
        ],
    ),
    ModuleSpec(
        key="reaction_roles",
        label="Reaction Roles",
        emoji="🔁",
        description="Self-assigned roles via emoji reactions.",
        settings=[
            SettingSpec("enabled", "Reaction roles", "toggle", default=False,
                        description="Master switch. Manage pairs with /reaction_role_add."),
            SettingSpec("channel", "Reaction message channel", "channel",
                        description="Where the reaction message is posted."),
        ],
    ),
    ModuleSpec(
        key="message_quoter",
        label="Message Quoter",
        emoji="💬",
        description="Reply-quoting of messages.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("embed_color", "Embed color", "text",
                        description="Hex color, e.g. 96edf1",
                        default="96edf1"),
            SettingSpec("require_reply", "Require reply", "toggle", default=True,
                        description="Only quote messages when replied to."),
            SettingSpec("allowed_role_1", "Allowed role 1", "role",
                        description="First role allowed to use the quoter feature."),
            SettingSpec("allowed_role_2", "Allowed role 2", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_3", "Allowed role 3", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_4", "Allowed role 4", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_5", "Allowed role 5", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_6", "Allowed role 6", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_7", "Allowed role 7", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_8", "Allowed role 8", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_9", "Allowed role 9", "role",
                        description="Additional role allowed to use the quoter feature."),
            SettingSpec("allowed_role_10", "Allowed role 10", "role",
                        description="Additional role allowed to use the quoter feature."),
        ],
    ),
    ModuleSpec(
        key="spotify",
        label="Spotify",
        emoji="🎵",
        description=(
            "Auto-embed Spotify track links posted in chat. "
            "Also available anywhere via `/spotify`."
        ),
        settings=[
            SettingSpec(
                "enabled",
                "Enabled",
                "toggle",
                default=True,
                description="Replace Spotify track links in chat with a rich embed.",
            ),
        ],
    ),
    ModuleSpec(
        key="acc_link",
        label="Account Linking",
        emoji="🔗",
        description="Link and display public GitHub and Steam profiles.",
        settings=[
            SettingSpec(
                "enabled",
                "Enabled",
                "toggle",
                default=False,
                description="Enable account-linking commands and profile lookups.",
            ),
            SettingSpec(
                "channel",
                "Allowed channel",
                "channel",
                description="Only allow account lookups in this channel. Leave unset for all channels.",
            ),
        ],
    ),
    ModuleSpec(
        key="utilities",
        label="Utilities",
        emoji="🧰",
        description="Misc helper commands.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
        ],
    ),
    ModuleSpec(
        key="report_msg",
        label="Report Message",
        emoji="📣",
        description="Let users report a message to staff. If you are looking for the moderator ping," \
        "check the Moderation module instead.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("report_channel", "Reports channel", "channel"),
            SettingSpec("staff_role", "Staff role", "role"),
            SettingSpec("anonymous", "Anonymous reports", "toggle", default=False),
            SettingSpec("cooldown", "Cooldown (seconds)", "integer", default=30,
                        description="Per-user report cooldown."),
        ],
    ),
    ModuleSpec(
        key="server_stats",
        label="Server Stats",
        emoji="📊",
        description="Live member / channel counters.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("category", "Stats category", "channel"),
            SettingSpec("show_total", "Show total members", "toggle", default=True),
            SettingSpec("show_online", "Show online members", "toggle", default=True),
            SettingSpec("update_interval", "Update interval (minutes)", "integer", default=10),
        ],
    ),
    ModuleSpec(
        key="temporary_voice",
        label="Temporary Voice",
        emoji="🔊",
        description="Join-to-create private voice channels.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("join_channel", "Join-to-create channel", "channel"),
            SettingSpec("category", "Channel category", "channel"),
            SettingSpec("rename_on_join", "Rename on join", "toggle", default=True,
                        description="Name the channel after its owner."),
            SettingSpec("default_name", "Default channel name", "text",
                        default="{user}'s channel",
                        description="Use {user} for the owner's name."),
        ],
    ),
    ModuleSpec(
        key="rules",
        label="Rules",
        emoji="📜",
        description="Custom rules and rule messages. Create rules with `/rule_add`, "
        "then send them using `/rules`",
        settings=[
            SettingSpec(
                "enabled",
                "Enabled",
                "toggle",
                default=False,
            ),
            SettingSpec(
                "rules_channel",
                "Rules channel",
                "channel",
            ),
            SettingSpec(
                "member_role",
                "Member role (on accept)",
                "role",
            ),
            SettingSpec(
                "react_to_accept",
                "React to accept",
                "toggle",
                default=True,
            ),
            SettingSpec(
                "default_format",
                "Default rule format",
                "text",
                default="embed",
                description="embed or text",
            ),
        ],
    ),
    ModuleSpec(
        key="tickets",
        label="Tickets",
        emoji="🎫",
        description=(
            "Support ticket panels. Configure the settings below, "
            "then use `/ticket_panel` to post the Create Ticket button."
        ),
        settings=[
            SettingSpec(
                "enabled",
                "Ticket system",
                "toggle",
                default=True,
                description="Master switch for creating tickets.",
            ),
            SettingSpec(
                "panel_channel",
                "Panel channel",
                "channel",
                description=(
                    "Where `/ticket_panel` posts the ticket panel."
                ),
            ),
            SettingSpec(
                "support_role",
                "Support role",
                "role",
                description="Role with access to all tickets.",
            ),
            SettingSpec(
                "ping_support_role",
                "Ping support role",
                "toggle",
                default=False,
                description=(
                    "Mention the Support role when a ticket is created."
                ),
            ),
            SettingSpec(
                "blacklisted_role",
                "Blacklisted role",
                "role",
                description=(
                    "Role that cannot create tickets (optional)."
                
                ),
            ),
        ],
    ),
    ModuleSpec(
        key="message_relay",
        label="Message Relay",
        emoji="📡",
        description=(
        "Manual cross-server message relay. Configure source and target "
        "servers with `/relay_setup`, then manage channels with relay commands."
    ),
        settings=[
            SettingSpec(
                "enabled",
                "Enabled",
                "toggle",
                default=False,
                description="Master switch for relaying messages.",
            ),
            SettingSpec(
                "relay_bots",
                "Relay bot messages",
                "toggle",
                default=False,
            ),
            SettingSpec(
                "filter",
                "Filtered words",
                "text",
             description="Comma-separated words to block from relays.",
            ),
        ],
    ),
    ModuleSpec(
        key="moderation",
        label="Moderation",
        emoji="🛡️",
        description="Moderation commands + logging.",
        settings=[
            SettingSpec("enabled", "Logging enabled", "toggle", default=True,
                        description="Master switch for mod-log messages."),
            SettingSpec("log_channel", "Mod log channel", "channel"),
            SettingSpec("mod_role", "Moderator role", "role"),
            SettingSpec("head_mod_role", "Head moderator role", "role"),
            SettingSpec(
                        "ping_mod_role",
                        "Ping moderator role",
                        "toggle",
                        default=False,
                        description=(
                        "Mention the Moderator role when a report is sent."
                        ),
            ),
            SettingSpec("embed_color", "Log embed color", "text",
                        description="Hex color, e.g. 96edf1",
                        default="96edf1"),
        ],
    ),
    ModuleSpec(
        key="mentions",
        label="Mention Protection",
        emoji="🔔",
        description=(
            "Prevent users from pinging protected members or roles. "
            "Configure protected and exempt roles below."
        ),
        settings=[
            SettingSpec(
                "enabled",
                "Enabled",
                "toggle",
                default=False,
                description="Enable mention protection.",
            ),
            SettingSpec(
                "blocked_role",
                "Protected role",
                "role",
                description=(
                "Members with this role cannot be pinged, and the role "
                "itself cannot be mentioned."
                ),
            ),
            SettingSpec(
                "whitelist_role",
                "Ping bypass role",
                "role",
                description=(
                    "Members with this role can ping protected members and roles."
                ),
            ),
            SettingSpec(
                "timeout_minutes",
                "Timeout duration (minutes)",
                "integer",
                default=60,
                description="Timeout applied after a mention-protection violation.",
            ),
            SettingSpec(
                "delete_warning_after",
                "Warning delete delay (seconds)",
                "integer",
                default=8,
                description="Set to 0 to leave the warning message visible.",
            ),
        ],
    ),
    ModuleSpec(
        key="member_commands",
        label="Member Commands",
        emoji="👥",
        description="Member info commands.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("show_join_date", "Show join date", "toggle", default=True),
            SettingSpec("show_roles", "Show roles", "toggle", default=True),
        ],
    ),
    ModuleSpec(
        key="help",
        label="Help",
        emoji="❓",
        description="Custom help command.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("help_channel", "Help channel", "channel"),
            SettingSpec("dm_help", "DM help instead of channel", "toggle", default=False),
        ],
    ),
    ModuleSpec(
        key="leveling",
        label="Leveling",
        emoji="📈",
        description="XP, levels, and level-up messages.",
        settings=[
            SettingSpec("enabled", "XP earning", "toggle", default=True,
                        description="Master switch for earning XP."),
            SettingSpec("level_up_channel", "Level-up channel", "channel",
                        description="Where level-up messages are posted."),
            SettingSpec("level_up_message", "Level-up message", "text",
                        default="🎉 {user} just reached level {level}!",
                        description="Use {user} and {level}."),
            SettingSpec("xp_cooldown", "XP cooldown (seconds)", "integer", default=15,
                        description="Time before XP can be earned again."),
            SettingSpec("weekly_channel", "Weekly leaderboard channel", "channel",
                        description="Schedule the time via /weekly_config."),
        ],
    ),
    ModuleSpec(
        key="weather",
        label="Weather",
        emoji="🌤️",
        description="Weather lookups.",
        settings=[
            SettingSpec("enabled", "Enabled", "toggle", default=False),
            SettingSpec("location", "Default location", "text",
                        description="City or coordinates."),
            SettingSpec("units", "Default units", "text",
                        description="metric or imperial",
                        default="metric"),
            SettingSpec("show_humidity", "Show humidity", "toggle", default=True),
        ],
    ),
    ModuleSpec(
        key="setup_ui",
        label="Setup Dashboard",
        emoji="⚙️",
        description="This dashboard itself.",
        settings=[
            SettingSpec("title", "Dashboard title", "text",
                        default="Bot Setup Dashboard",
                        description="Title shown at the top of this panel."),
            SettingSpec("admin_only", "Admin only", "toggle", default=True,
                        description="Require Manage Server to interact."),
        ],
    ),
]


def get_module(key: str) -> Optional[ModuleSpec]:
    for module in MODULES:
        if module.key == key:
            return module
    return None


def is_message_quoter_role_setting(spec: SettingSpec) -> bool:
    """Return whether a setting is one of the Message Quoter role slots."""
    return (
        spec.kind == "role"
        and spec.key.startswith(MESSAGE_QUOTER_ROLE_PREFIX)
        and spec.key[len(MESSAGE_QUOTER_ROLE_PREFIX):].isdigit()
        and 1 <= int(spec.key[len(MESSAGE_QUOTER_ROLE_PREFIX):]) <= MAX_MESSAGE_QUOTER_ROLES
    )


def message_quoter_role_number(spec: SettingSpec) -> int:
    """Return the numeric slot for a Message Quoter role setting."""
    return int(spec.key[len(MESSAGE_QUOTER_ROLE_PREFIX):])


def get_message_quoter_role_specs() -> list[SettingSpec]:
    """Return the Message Quoter role settings in slot order."""
    module = get_module(MESSAGE_QUOTER_MODULE_KEY)

    if module is None:
        return []

    return sorted(
        (
            spec
            for spec in module.settings
            if is_message_quoter_role_setting(spec)
        ),
        key=message_quoter_role_number,
    )


def get_visible_message_quoter_role_specs(
    store: SetupConfigStore,
    guild: discord.Guild,
) -> list[SettingSpec]:
    """
    Show configured roles plus exactly one next empty role slot.

    Example:
        none configured -> role 1
        role 1 configured -> roles 1 and 2
        roles 1-3 configured -> roles 1-4
    """
    role_specs = get_message_quoter_role_specs()
    visible_specs: list[SettingSpec] = []

    for spec in role_specs:
        role_id = store.get(
            guild.id,
            MESSAGE_QUOTER_MODULE_KEY,
            spec.key,
            None,
        )
# balls y u reading this lol
        if role_id is not None:
            visible_specs.append(spec)
            continue

        # Show only the first empty slot after all previous slots.
        if not visible_specs:
            return [spec]

        previous_spec = role_specs[len(visible_specs) - 1]
        previous_role_id = store.get(
            guild.id,
            MESSAGE_QUOTER_MODULE_KEY,
            previous_spec.key,
            None,
        )

        if previous_role_id is not None:
            visible_specs.append(spec)

        return visible_specs

    return visible_specs


# ============================================================ Value formatting helpers

def format_value(guild: discord.Guild, value) -> str:
    """Human-readable display of a stored setting value."""
    if value is None:
        return "Not set"
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, int):
        return str(value)
    return str(value)


def format_channel(guild: Optional[discord.Guild], channel_id) -> str:
    if channel_id is None:
        return "Not set"
    if guild is not None:
        channel = guild.get_channel(int(channel_id))
        if channel is not None:
            return channel.mention
    return f"<#{channel_id}>"


def format_role(guild: Optional[discord.Guild], role_id) -> str:
    if role_id is None:
        return "Not set"
    if guild is not None:
        role = guild.get_role(int(role_id))
        if role is not None:
            return role.mention
    return f"<@&{role_id}>"


def display_setting(guild: Optional[discord.Guild], spec: SettingSpec, value) -> str:
    """Format a setting value according to its kind."""
    if value is None:
        value = spec.default
    if spec.kind == "toggle":
        return "On" if bool(value) else "Off"
    if spec.kind == "channel":
        return format_channel(guild, value)
    if spec.kind == "role":
        return format_role(guild, value)
    if value is None:
        return "Not set"
    return str(value)


# ============================================================ Embed builders

def build_main_embed(store: SetupConfigStore, guild: discord.Guild) -> discord.Embed:
    title = store.get(guild.id, "setup_ui", "title", default="Bot Setup Dashboard")
    embed = discord.Embed(
        title=str(title),
        description=(
            "Pick a feature from the dropdown below to configure it.\n"
            "Everything is saved automatically to the bot's database."
        ),
        color=EMBED_COLOR,
    )
    embed.set_footer(text=f"{guild.name} • Use the menu below")
    return embed


def build_module_embed(
    store: SetupConfigStore,
    guild: discord.Guild,
    module: ModuleSpec,
) -> discord.Embed:
    enabled_spec = next(
        (s for s in module.settings if s.key == "enabled"),
        None,
    )

    description = module.description

    if enabled_spec is not None:
        enabled = bool(
            store.get(
                guild.id,
                module.key,
                "enabled",
                default=enabled_spec.default,
            )
        )
        status = "🟢 Enabled" if enabled else "🔴 Disabled"
        description += f"\n\n**Status:** {status}"

    embed = discord.Embed(
        title=f"{module.emoji} {module.label}",
        description=description,
        color=EMBED_COLOR,
    )
    embed.set_footer(text="Click a button to edit • ◀ Back returns to the dashboard")

    values = store.get_module(guild.id, module.key)

    # For Message Quoter, only show configured role slots plus one empty slot
    if module.key == MESSAGE_QUOTER_MODULE_KEY:
        visible_role_specs = get_visible_message_quoter_role_specs(
            store,
            guild,
        )

        visible_role_keys = {
            spec.key
            for spec in visible_role_specs
        }

        settings = [
            spec
            for spec in module.settings
            if not is_message_quoter_role_setting(spec)
            or spec.key in visible_role_keys
        ]
    else:
        settings = module.settings

    for spec in settings:
        current = values.get(spec.key, spec.default)

        embed.add_field(
            name=f"{spec.label}",
            value=f"`{display_setting(guild, spec, current)}`"
                  + (f"\n{spec.description}" if spec.description else ""),
            inline=False,
        )
    return embed


def build_select_embed(module: ModuleSpec, spec: SettingSpec) -> discord.Embed:
    kind_label = {
        "channel": "Pick a channel",
        "role": "Pick a role",
    }.get(spec.kind, spec.label)
    embed = discord.Embed(
        title=f"{module.emoji} {module.label} – {spec.label}",
        description=f"{kind_label} for **{spec.label}**.",
        color=EMBED_COLOR,
    )
    embed.set_footer(text="◀ Back cancels and returns to the module panel")
    return embed


# ============================================================ Views

class AdminOnlyView(discord.ui.View):
    """Base view that restricts interaction to Manage Server holders or bot owner."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This can only be used inside a server.",
                ephemeral=True,
            )
            return False

        # Allow bot owner regardless of guild permissions
        if is_bot_owner(interaction.user):
            return True

        if interaction.user.guild_permissions.manage_guild:
            return True

        await interaction.response.send_message(
            "You need the **Manage Server** permission to configure the bot.",
            ephemeral=True,
        )
        return False


class MainView(AdminOnlyView):
    """The dashboard root: a dropdown listing every module."""

    def __init__(self, cog: "SetupUICog"):
        super().__init__(timeout=600)
        self.cog = cog
        self.add_item(self._build_select())

    def _build_select(self) -> discord.ui.Select:
        options = [
            discord.SelectOption(
                label=module.label,
                description=module.description[:100],
                emoji=module.emoji or None,  # Convert empty string to None
                value=module.key,
            )
            for module in MODULES
        ]
        select = discord.ui.Select(
            placeholder="Select a feature to configure…",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self._on_select
        return select

    async def _on_select(self, interaction: discord.Interaction) -> None:
        try:
            module_key = interaction.data["values"][0]
            module = get_module(module_key)
            if module is None:
                await interaction.response.send_message(
                    "Unknown feature.", ephemeral=True
                )
                return
            embed = build_module_embed(self.cog.store, interaction.guild, module)
            view = ModuleView(self.cog, module_key, interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"[setup_ui] Error in MainView._on_select: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while opening the panel. Check the bot console.",
                    ephemeral=True,
                )


class ModuleView(AdminOnlyView):
    """A single module's config panel: one button per setting + Back."""

    def __init__(self, cog: "SetupUICog", module_key: str, guild: discord.Guild):
        super().__init__(timeout=600)
        self.cog = cog
        self.module_key = module_key
        self.guild = guild
        module = get_module(module_key)
        if module is None:
            return

        # For Message Quoter, only show configured role slots plus one empty slot
        if module_key == MESSAGE_QUOTER_MODULE_KEY:
            role_specs = get_visible_message_quoter_role_specs(
                cog.store,
                guild,
            )
            visible_role_keys = {
                spec.key
                for spec in role_specs
            }

            settings = [
                spec
                for spec in module.settings
                if not is_message_quoter_role_setting(spec)
                or spec.key in visible_role_keys
            ]
        else:
            settings = module.settings

        for spec in settings:
            self.add_item(self._make_button(module, spec))

        back = discord.ui.Button(label="◀ Back to dashboard", style=discord.ButtonStyle.secondary)
        back.callback = self._on_back
        self.add_item(back)

    def _make_button(self, module: ModuleSpec, spec: SettingSpec) -> discord.ui.Button:
        current = self.cog.store.get(self.guild.id, module.key, spec.key, spec.default)
        value_text = display_setting(self.guild, spec, current)
        label = f"{spec.label}: {value_text}"[:80]

        if spec.kind == "toggle":
            style = discord.ButtonStyle.success if bool(current) else discord.ButtonStyle.danger
        else:
            style = discord.ButtonStyle.primary

        button = discord.ui.Button(
            label=label,
            style=style,
            custom_id=f"setup_{module.key}_{spec.key}",
        )
        button.callback = self._make_callback(module, spec)
        return button

    def _make_callback(self, module: ModuleSpec, spec: SettingSpec) -> Callable:
        cog = self.cog
        module_key = self.module_key

        async def callback(interaction: discord.Interaction) -> None:
            try:
                guild = interaction.guild
                if spec.kind == "toggle":
                    new_val = not cog.store.get(guild.id, module_key, spec.key, spec.default)
                    cog.store.set(guild.id, module_key, spec.key, new_val)
                    await self._refresh(interaction)
                    await interaction.followup.send(
                        f"✅ {spec.label} is now {'On' if new_val else 'Off'}.",
                        ephemeral=True,
                    )
                elif spec.kind in ("text", "integer"):
                    modal = TextModal(cog, module_key, spec, guild)
                    await interaction.response.send_modal(modal)
                elif spec.kind == "channel":
                    embed = build_select_embed(module, spec)
                    view = ChannelSelectView(cog, module_key, spec.key, guild)
                    await interaction.response.edit_message(embed=embed, view=view)
                elif spec.kind == "role":
                    embed = build_select_embed(module, spec)
                    view = RoleSelectView(cog, module_key, spec.key, guild)
                    await interaction.response.edit_message(embed=embed, view=view)
            except Exception as e:
                print(f"[setup_ui] Error in ModuleView callback: {type(e).__name__}: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while applying that setting. Check the bot console.",
                        ephemeral=True,
                    )

        return callback

    async def _on_back(self, interaction: discord.Interaction) -> None:
        try:
            embed = build_main_embed(self.cog.store, interaction.guild)
            view = MainView(self.cog)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"[setup_ui] Error in ModuleView._on_back: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while going back. Check the bot console.",
                    ephemeral=True,
                )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        try:
            module = get_module(self.module_key)
            embed = build_module_embed(self.cog.store, interaction.guild, module)
            view = ModuleView(self.cog, self.module_key, interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"[setup_ui] Error in ModuleView._refresh: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while refreshing the panel. Check the bot console.",
                    ephemeral=True,
                )


class _BackToModuleButton(discord.ui.Button):
    def __init__(self, parent: "ChannelSelectView | RoleSelectView"):
        super().__init__(label="◀ Back", style=discord.ButtonStyle.secondary)
        self._parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            cog = self._parent.cog
            module_key = self._parent.module_key
            module = get_module(module_key)
            embed = build_module_embed(cog.store, interaction.guild, module)
            view = ModuleView(cog, module_key, interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(f"[setup_ui] Error in _BackToModuleButton: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while going back. Check the bot console.",
                    ephemeral=True,
                )


class ChannelSelectView(AdminOnlyView):
    def __init__(self, cog: "SetupUICog", module_key: str, setting_key: str, guild: discord.Guild):
        super().__init__(timeout=600)
        self.cog = cog
        self.module_key = module_key
        self.setting_key = setting_key
        self.guild = guild

        select = discord.ui.ChannelSelect(
            placeholder="Select a channel…",
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)
        self.add_item(_BackToModuleButton(self))

    async def _on_select(self, interaction: discord.Interaction) -> None:
        try:
            channel_id = interaction.data["values"][0]
            channel = await interaction.guild.fetch_channel(int(channel_id))
            self.cog.store.set(
                interaction.guild.id, self.module_key, self.setting_key, channel.id
            )
            module = get_module(self.module_key)
            embed = build_module_embed(self.cog.store, interaction.guild, module)
            view = ModuleView(self.cog, self.module_key, interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)
            await interaction.followup.send(
                f"✅ Set to {channel.mention}.", ephemeral=True
            )
        except Exception as e:
            print(f"[setup_ui] Error in ChannelSelectView._on_select: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while saving the channel. Check the bot console.",
                    ephemeral=True,
                )


class RoleSelectView(AdminOnlyView):
    def __init__(self, cog: "SetupUICog", module_key: str, setting_key: str, guild: discord.Guild):
        super().__init__(timeout=600)
        self.cog = cog
        self.module_key = module_key
        self.setting_key = setting_key
        self.guild = guild

        select = discord.ui.RoleSelect(
            placeholder="Select a role…",
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)
        self.add_item(_BackToModuleButton(self))

    async def _on_select(self, interaction: discord.Interaction) -> None:
        try:
            role_id = interaction.data["values"][0]
            role = interaction.guild.get_role(int(role_id))
            if role is None:
                await interaction.response.send_message(
                    "That role could not be found.",
                    ephemeral=True,
                )
                return
            self.cog.store.set(
                interaction.guild.id, self.module_key, self.setting_key, role.id
            )
            module = get_module(self.module_key)
            embed = build_module_embed(self.cog.store, interaction.guild, module)
            view = ModuleView(self.cog, self.module_key, interaction.guild)
            await interaction.response.edit_message(embed=embed, view=view)
            await interaction.followup.send(
                f"✅ Set to {role.mention}.", ephemeral=True
            )
        except Exception as e:
            print(f"[setup_ui] Error in RoleSelectView._on_select: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while saving the role. Check the bot console.",
                    ephemeral=True,
                )


class TextModal(discord.ui.Modal):
    """Modal for `text` and `integer` settings."""

    def __init__(self, cog: "SetupUICog", module_key: str, spec: SettingSpec, guild: discord.Guild):
        title = f"{spec.label}"[:45]
        super().__init__(title=title)
        self.cog = cog
        self.module_key = module_key
        self.spec = spec
        self.guild = guild

        current = cog.store.get(guild.id, module_key, spec.key, spec.default)
        placeholder = "Enter a value…" if spec.kind == "text" else "Enter a number…"
        self.input = discord.ui.TextInput(
            label=spec.label[:45],
            placeholder=placeholder,
            default=str(current) if current is not None else None,
            required=False,
            max_length=4000,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            raw = self.input.value.strip()
            guild = interaction.guild

            if self.spec.kind == "integer":
                if raw == "":
                    value = None
                else:
                    try:
                        value = int(raw)
                    except ValueError:
                        await interaction.response.send_message(
                            "❌ Please enter a whole number.", ephemeral=True
                        )
                        return
            else:
                value = raw if raw != "" else None
# nick-grrr 
            self.cog.store.set(guild.id, self.module_key, self.spec.key, value)

            module = get_module(self.module_key)
            embed = build_module_embed(self.cog.store, guild, module)
            view = ModuleView(self.cog, self.module_key, guild)
            await interaction.response.edit_message(embed=embed, view=view)
            await interaction.followup.send(
                f"✅ {self.spec.label} updated.", ephemeral=True
            )
        except Exception as e:
            print(f"[setup_ui] Error in TextModal.on_submit: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while saving the value. Check the bot console.",
                    ephemeral=True,
                )


# ============================================================ Cog

class SetupUICog(commands.Cog):
    """Posts the interactive setup dashboard via /setup."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = SetupConfigStore(DB_PATH)

    @app_commands.command(
        name="setup",
        description="Open the interactive bot setup dashboard.",
    )
    @app_commands.guild_only()
    @owner_or_has_permissions(manage_guild=True)
    async def setup_dashboard(self, interaction: discord.Interaction) -> None:
        embed = build_main_embed(self.store, interaction.guild)
        view = MainView(self)
        await interaction.response.send_message(embed=embed, view=view)

    @setup_dashboard.error
    async def setup_dashboard_error(
        self, interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the **Manage Server** permission to use /setup "
                "(unless you are the bot owner).",
                ephemeral=True,
            )
            return
        print(f"[setup_ui] Unexpected error in /setup: {type(error).__name__}: {error}")
        await interaction.response.send_message(
            "An unexpected error occurred. Check the bot console.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        print(
            f"[setup_ui] View/callback error: "
            f"{type(error).__name__}: {error}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupUICog(bot))