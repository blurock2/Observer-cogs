# Observer

**Moderation. Security. Logging. Server management. All in one Discord bot.**

Observer is a modular Discord bot designed to help communities stay **secure, organized, and easy to manage**. From everyday moderation to detailed audit trails, raid protection, member onboarding, and quality-of-life utilities, Observer brings essential server tools together in one configurable system.

Built with **Python** and **discord.py**, Observer uses persistent per-server settings and an integrated Discord setup interface. Server administrators can configure supported features without having to edit configuration files.

> **Actively developed:** Observer receives ongoing fixes and improvements. Features may evolve between releases.

---

## Why Observer?

- **Moderation with accountability:** Ban, kick, timeout, track moderation actions, review cases, and undo supported actions with appropriate permissions.
- **Know what's happening:** Configurable audit logging helps staff follow changes to members, channels, messages, roles, and other server activity.
- **Protection beyond basic filters:** Join-burst detection, verification holds, honeypot enforcement, suspicious-link checks, and protected-mention controls.
- **One place to configure your server:** Manage modules, channels, staff roles, and feature settings through the in-Discord `/setup` interface.
- **Community management built in:** Reports, tickets, verification, reaction roles, leveling, and voice-channel automation.
- **Settings that survive restarts:** SQLite-backed per-server configuration, moderation records, and persistent feature data.

## Featured capabilities

### Moderation & case management

Give your moderation team the tools to act quickly while keeping decisions traceable.

- **Ban, kick, and timeout** users with permission-controlled moderation commands.
- **Persistent moderation records and cases** for reviewing action history and reasons.
- **Moderation statistics** for following staff activity.
- **Configurable moderation logs** that record supported actions.
- **Undo controls** for supported actions, restricted to authorized head moderators.
- **Channel management tools**, including locks, unlocks, slowmode, and lockdown controls.
- **Role-based access controls** that distinguish moderation responsibilities.

### Audit logging & server visibility

Observer can record important server events in configurable logging channels, helping staff investigate incidents and understand what changed.

| Area | What Observer can record |
| --- | --- |
| **Messages** | Supported message edits, deletions, and message activity |
| **Members** | Joins, leaves, kicks, bans, and other supported member events |
| **Channels** | Channel creation, deletion, updates, and permission changes |
| **Roles** | Supported role changes and updates |
| **Server assets** | Webhook, emoji, and sticker changes |
| **Moderation** | Actions, recorded reasons, and associated case information |

Where Discord provides sufficient audit-log information, Observer attempts to identify the responsible actor. Event coverage and actor attribution depend on Discord permissions, gateway events, and available audit-log data.

### Anti-raid & server protection

Protect the server against disruptive join bursts, suspicious messages, and unwanted abuse.

- **Join-burst detection:** Detect unusually high numbers of new members within a configured time window.
- **Automatic raid holds:** Apply temporary verification holds and configured restrictions when a raid threshold is reached.
- **Honeypot channels:** Detect members posting in a designated trap channel and apply configured enforcement, subject to exemptions and Discord permissions.
- **Security alerts:** Report anti-raid and honeypot events to a configured channel.
- **Scam and suspicious-link checks:** Help identify and moderate supported phishing, scam, and unsafe-link patterns.
- **Protected-mention enforcement:** Apply configured controls around mentions of sensitive roles.
- **Staff exemptions and permission checks:** Respect designated bypass roles and Discord's role hierarchy.

Protection is configurable and is not a guarantee against every raid, scam, or malicious message.

### Server setup, onboarding & automation

Manage the server without having to juggle multiple bots or hand-edit configuration files.

- **Integrated `/setup` interface** for supported modules and per-server settings.
- **Independent autorole and verification workflows:** Assign join roles and verified roles based on separate settings.
- **Button-based member verification** with configurable role and channel behavior.
- **Reaction roles** for self-assignable community roles.
- **Custom server rules** with configurable presentation and acceptance workflows.
- **Temporary voice channels** that members can create by joining a designated channel.
- **Server statistics and member counters** to display community activity.
- **Temporary roles** that expire after a configured duration.

### Reports & support tickets

Keep community reports and support requests organized.

- **Message reporting** through Discord's message context menu.
- **Staff report workflows** for claiming and managing reports.
- **Anonymous reporting options**, cooldowns, and configurable destinations.
- **Support ticket panels** with dedicated ticket channels and staff-role access.
- **Moderation-related case records** to help track incidents and actions.

### Community & everyday utilities

Observer isn't only for administrators. It includes tools members can use every day.

| Category | Features |
| --- | --- |
| **Engagement** | XP leveling, leaderboards, rankings, and level rewards |
| **Personal organization** | Reminders, bookmarks, to-do lists, and AFK statuses |
| **Profiles** | Member information, user IDs, avatars, and account-linking features |
| **Messages** | Message quoting and configurable message relays |
| **Relay management** | Multiple source and destination channels, cross-server relays, and content filtering |
| **Information** | Weather, server statistics, and Spotify display features |
| **Reusable content** | Tags and other configurable community utilities |

Observer also includes GitHub, Steam, and Spotify account-linking functionality and optional application-bridge infrastructure.

---

## Built for server administrators

Observer is **modular**, but its features work together through a shared configuration system.

1. **Add Observer** to your Discord server with the permissions its enabled features need.
2. **Open `/setup`** and configure your moderation roles, log channels, verification, protection, and community modules.
3. **Enable the features you need.** Different servers can use different configurations.
4. **Keep your configuration.** Settings and persistent records are stored in SQLite and survive normal bot restarts.

Some actions require specific Discord permissions, intents, or a sufficiently high bot role.

---

## Architecture

Observer is built around independently maintained **discord.py cogs** in `cogs/`, with shared configuration and persistence services.

**Core design principles**

- **Modular:** Individual systems can be maintained without rewriting the entire bot.
- **Configurable:** Guilds have independent settings instead of a single global configuration.
- **Persistent:** SQLite stores server configuration and supported feature data.
- **Practical:** Features prioritize real moderation and community-management workflows.
- **Self-contained:** Core features do not require paid AI APIs.
- **Maintainable:** Automated tests and isolated modules help support continuous development.

Project layout (selected files):

```text
Observer-cogs/
├── bot.py
├── database.py
├── logging_config.py
├── cogs/
│   ├── setup_ui.py
│   ├── moderation.py
│   ├── audit_log.py
│   ├── anti_raid.py
│   ├── security.py
│   ├── report_msg.py
│   ├── tickets.py
│   ├── autoroles.py
│   ├── leveling.py
│   ├── message_relay.py
│   ├── reminder.py
│   └── ...
├── tests/
├── deploy/
├── .env.example
├── requirements.txt
└── VERSION
```

The local SQLite database stores supported guild configuration, moderation data, feature state, and other persistent records. Runtime databases and secrets should not be committed to source control.

---

## Self-hosting

### 1. Clone the repository

```bash
git clone https://github.com/blurock2/Observer-cogs.git
cd Observer-cogs
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
```

**Linux / macOS**
```bash
source .venv/bin/activate
```

**Windows**
```powershell
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure credentials

Copy `.env.example` to `.env` and fill in the values required by your installation.

**Linux / macOS**
```bash
cp .env.example .env
```

**Windows**
```powershell
copy .env.example .env
```

At minimum, configure your Discord bot token and bot owner ID as described in the example environment file. **Never commit actual tokens or credentials.**

### 5. Launch Observer

```bash
python bot.py
```

Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications) first. Enable the gateway intents and permissions required by the features you intend to use.

See the `deploy/` directory for deployment-related files. Persistent hosting should preserve the SQLite databases and runtime data between restarts.

---

## Development & contributing

Observer is under active development. Issues, improvements, and feature suggestions are welcome through GitHub.

Install dependencies and run the test suite with:

```bash
pip install -r requirements.txt
python -m pytest
```

For linting, if Ruff is installed:

```bash
ruff check .
```

When contributing, keep new features modular, respect existing per-guild settings, avoid unnecessary dependencies, and include tests for meaningful behavior changes.

## Disclaimer

Observer is an independent Discord bot project and is not affiliated with or endorsed by Discord Inc.

## License

Free use with credits to the original creator.

---

<p align="center">
  <strong>Observer</strong><br>
  Moderation • Audit Logging • Anti-Raid • Server Management • Community Utilities
</p>


## Observer website

The public landing page and Discord sign-in server picker are served by the bot's aiohttp web service. Build the frontend on the VPS with Node.js installed:

```bash
cd /opt/observer-bot/activity
npm install
npm run build
```

Set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and `PUBLIC_BASE_URL=https://observer-dashboard.observer` in the bot's `.env`. Register `https://observer-dashboard.observer/auth/callback` in the Discord application's OAuth2 redirect URLs. The site lists only Discord servers owned by the signed-in account, checks whether Observer is present, and provides an invite when it is absent. If Observer is already present, the page opens the server and directs the owner to `/setup` in Discord; browser-based setup editing is not included yet.

Optional landing details are configured with `OBSERVER_CREATOR_NAME`, `OBSERVER_SUPPORT_URL`, and `OBSERVER_SOURCE_URL`.
