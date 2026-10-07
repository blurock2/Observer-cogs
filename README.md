# Observer

> A modular, configurable Discord bot built for moderation, automation, community management, and server utilities.

Observer is a multi-purpose Discord bot built with **Python** and **discord.py**.

Instead of relying on one massive command system, Observer is split into independently maintained **cogs**, allowing features to stay modular while sharing persistent per-server configuration through SQLite.

The goal is simple: provide the tools a Discord server actually needs without turning configuration into a mess.

> **Status:** Observer is actively developed. Features, commands, and configuration may change between versions.

---

## Features

### Moderation

Tools for handling moderation while keeping actions organized and traceable.

- Ban, kick, timeout, and other moderation commands
- Persistent moderation records
- Moderation statistics
- Mod logging
- Permission and role-based command restrictions
- Reversal / undo support where available

### Security & Audit Logging

Observer can monitor important server activity and provide moderation teams with additional visibility.

- Audit logging
- Security-related server tools
- Role and permission-aware protections
- Configurable access restrictions
- Server event tracking

### Server Configuration

Each server has its own persistent configuration.

- In-Discord setup interface
- Per-server settings stored in SQLite
- Configurable staff roles
- Configurable tester roles
- Module-specific configuration
- Bot access restrictions

No configuration files need to be manually edited for normal server setup.

### Tickets & Reports

Built-in systems for communication between members and staff.

- Ticket panels
- Configurable support roles
- Ticket channels
- Message reporting
- Anonymous reporting support
- Report cooldowns
- Configurable report destinations

### Community Tools

Features designed for everyday server management and member interaction.

- Reaction roles
- Server rules
- Tags
- Leveling
- Member information commands
- Mention tools and protection
- Temporary voice channels

### Messaging

Tools for working with messages across Discord.

- Message quoting
- Message relaying
- Multi-channel relay support
- Cross-server relay support
- Bot-message filtering
- Word filtering

### Utilities

General-purpose functionality that does not require external AI services.

- Reminders
- Server statistics
- Weather information
- Spotify display features
- In-Discord help system

### Integrations

Observer also contains infrastructure for features that communicate with external services.

- Account linking
- Application bridge
- Optional external-service integrations

---

## Modular Architecture

Observer uses Discord.py extensions located inside `cogs/`.

Each major feature is separated into its own module:

```text
Observer-cogs/
├── bot.py
├── database.py
├── logging_config.py
├── vps_manager.py
│
├── cogs/
│   ├── setup_ui.py
│   ├── audit_log.py
│   ├── acc_link.py
│   ├── reaction_roles.py
│   ├── message_quoter.py
│   ├── utilities.py
│   ├── report_msg.py
│   ├── server_stats.py
│   ├── temporary_voice.py
│   ├── rules.py
│   ├── tickets.py
│   ├── reminder.py
│   ├── message_relay.py
│   ├── moderation.py
│   ├── security.py
│   ├── tags.py
│   ├── mentions.py
│   ├── member_commands.py
│   ├── help.py
│   ├── leveling.py
│   ├── weather.py
│   ├── spotify_show.py
│   └── app_bridge.py
│
├── tests/
├── deploy/
├── data/
├── .env.example
├── requirements.txt
└── VERSION
```

This structure keeps individual systems isolated and makes features easier to maintain, test, reload, and expand.

---

## Database

Observer uses **SQLite** for persistent storage.

The database layer handles data such as:

- Guild configuration
- Moderation records
- Leveling data
- Module settings
- Persistent feature state
- Database migrations
- Backups

Runtime database files are stored locally and should **not** be committed to the repository.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/blurock2/Observer-cogs.git
cd Observer-cogs
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it.

**Windows**

```bash
.venv\Scripts\activate
```

**Linux / macOS**

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the included example:

```bash
cp .env.example .env
```

On Windows:

```bash
copy .env.example .env
```

Then configure:

```env
DISCORD_TOKEN=your-discord-bot-token
BOT_OWNER_ID=your-discord-user-id
```

Never commit your real Discord token.

### 5. Start Observer

```bash
python bot.py
```

---

## Discord Bot Setup

Before running Observer, create an application through the Discord Developer Portal and add a bot user.

Observer requires the appropriate Discord permissions and gateway intents for the features you enable.

Once the bot is running, configuration can primarily be handled through Observer's in-Discord setup system.

---

## Development

Install the normal project dependencies:

```bash
pip install -r requirements.txt
```

The repository includes automated tests using **pytest**.

Run them with:

```bash
pytest
```

Code can also be checked with:

```bash
ruff check .
```

---

## Adding a Cog

Observer features are implemented as Discord.py extensions.

A basic cog follows the usual Discord.py extension structure:

```python
import discord
from discord.ext import commands


class Example(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(Example(bot))
```

Place new extensions inside:

```text
cogs/
```

Keep feature-specific logic inside its cog where possible and use the shared database layer for persistent data.

---

## Deployment

The `deploy/` directory contains deployment-related files for running Observer on a server/VPS.

Observer is designed to support persistent hosting where its SQLite databases and runtime data remain available between restarts.

---

## Project Goals

Observer is built around a few core principles:

- **Modular** — features should remain independently maintainable.
- **Persistent** — server configuration should survive restarts.
- **Configurable** — different servers should be able to use Observer differently.
- **Practical** — features should solve actual Discord server management problems.
- **Self-contained** — core functionality should not depend on paid AI APIs.
- **Maintainable** — adding new systems should not require rewriting existing ones.

---

## Contributing

Observer is still under active development.

Bug reports, improvements, and feature suggestions are welcome through GitHub Issues or Pull Requests.

When contributing:

1. Keep features modular.
2. Avoid unnecessary dependencies.
3. Preserve existing per-server configuration behavior.
4. Add or update tests where appropriate.
5. Run the test suite before submitting changes.

---

## Disclaimer

Observer is an independent Discord bot project and is not affiliated with or endorsed by Discord Inc.

---

## License

No license is currently specified in this repository.

If you intend for others to reuse or contribute to Observer, consider adding an open-source license such as MIT, Apache-2.0, or GPL-3.0.

---

<p align="center">
  <strong>Observer</strong><br>
  Moderation • Security • Automation • Community • Utilities
</p>
