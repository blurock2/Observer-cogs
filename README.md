# Observer

Observer is a modular Discord bot built with Python and `discord.py`. It provides configurable server-management, moderation, community, and utility features through independently loadable cogs.

> **Project status:** Active development. Core functionality is implemented and the project includes automated tests, but features, commands, and configuration may still change.

## Features

Observer currently includes modules for:

- Server setup and persistent per-server configuration
- Moderation tools and moderation statistics
- Ticket systems and message reporting
- Reaction roles, rules, mentions, and member commands
- Temporary voice channels
- Message quoting and message relaying
- Leveling
- Account linking
- Server statistics
- Weather and Spotify display features
- In-Discord help and an application bridge

Features are organised as Discord.py extensions in the `cogs/` directory, making them easier to maintain and reload during development.

## Project structure

```text
Observer-cogs/
├── bot.py                 # Application entry point and bot lifecycle
├── database.py            # Database helpers, migrations, and backups
├── logging_config.py      # Logging configuration
├── vps_manager.py         # VPS management utility
├── cogs/                  # Feature modules / Discord.py cogs
├── tests/                 # Pytest test suite
├── deploy/                # systemd service and sudoers deployment files
├── .env.example           # Required environment variable template
├── requirements.txt       # Python dependencies
└── VERSION                # Project version
```

## Requirements

- Python 3.10 or newer
- A Discord application and bot token
- Required privileged gateway intents enabled in the Discord Developer Portal:
  - Message Content Intent
  - Server Members Intent
  - Presence Intent

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/blurock2/Observer-cogs.git
   cd Observer-cogs
   ```

2. Create and activate a virtual environment:

   **Windows PowerShell**

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **macOS / Linux**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create your local environment file:

   **macOS / Linux**

   ```bash
   cp .env.example .env
   ```

   **Windows PowerShell**

   ```powershell
   Copy-Item .env.example .env
   ```

5. Update `.env` with your values:

   ```env
   DISCORD_TOKEN=your-bot-token
   BOT_OWNER_ID=your-discord-user-id
   ```

6. Start the bot:

   ```bash
   python bot.py
   ```

## Configuration

Observer uses:

- `.env` for secrets and global bot-owner configuration
- Local persistent storage for bot and guild configuration
- In-Discord setup tools for configuring supported modules per server

The bot creates and maintains local database data, including backups and migrations where required.

Never commit your real `.env` file or Discord bot token.

## Development

Run the automated tests with:

```bash
pytest
```

The repository uses:

- `pytest` and `pytest-asyncio` for tests
- `ruff` for linting
- Python logging for startup, extension loading, command synchronisation, and runtime error information

Lint the code with:

```bash
ruff check .
```

## Owner commands

The bot includes owner-only prefix commands for maintenance:

- `!reload_cogs` — reload configured extensions; extensions that failed during startup are retried
- `!sync_commands` — globally synchronise application commands after adding or changing slash commands
- `!restart` — notify configured log channels and restart the bot process
- `!debug_commands` — show locally registered and Discord-fetched application commands

## Deployment

The `deploy/` directory contains deployment-related files for Linux VPS hosting, including a systemd service configuration and sudoers configuration.

Review and adapt these files for your server, paths, Python environment, and Linux user before enabling the service.

## Notes

- Slash commands are loaded locally when the bot starts. Run `!sync_commands` after adding or changing application commands to publish them globally.
- Global Discord command updates can take time to propagate.
- `!reload_cogs` reloads extensions but does not automatically synchronise slash commands.
- Extensions that fail to load are logged, allowing the remaining bot features to continue starting.
- Some features require specific Discord permissions, channel configuration, roles, or external service setup.
- This project is primarily intended as a self-hosted bot; test changes in a development server before using them in a live community.

## Contributing

Issues, bug reports, feature suggestions, and pull requests are welcome.

Please keep changes focused, run the test suite, and lint the code before opening a pull request:

```bash
pytest
ruff check .
```
