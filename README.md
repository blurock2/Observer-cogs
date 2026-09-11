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
├── bot.py              # Application entry point and bot lifecycle
├── cogs/               # Feature modules / Discord.py cogs
├── tests/              # Pytest test suite
├── .env.example        # Required environment variable template
├── requirements.txt    # Python dependencies
└── VERSION             # Project version
```

## Requirements

- Python 3.10 or newer recommended
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

   ```bash
   cp .env.example .env
   ```

   On Windows, you can also copy `.env.example` manually and rename it to `.env`.

5. Update `.env`:

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

Never commit your real `.env` file or Discord bot token.

## Development

Run the automated tests with:

```bash
pytest
```

The repository uses:

- `pytest` and `pytest-asyncio` for tests
- `ruff` for linting
- Python logging for startup, extension-loading, command-sync, and runtime error information

Lint the code with:

```bash
ruff check .
```

## Owner commands

The bot includes owner-only prefix commands for maintenance:

- `!reload_cogs` — reload configured extensions and re-sync slash commands
- `!restart` — notify configured log channels and restart the process
- `!debug_commands` — show locally registered and Discord-synced application commands

## Notes

- Slash commands are globally synchronised when the bot starts and after cog reloads.
- Extensions that fail to load are logged, allowing the remaining bot features to continue starting.
- Some features require specific Discord permissions, channel configuration, roles, or external service setup.
- This project is primarily intended as a self-hosted bot; test changes in a development server before using them in a live community.

## Contributing

Issues, bug reports, feature suggestions, and pull requests are welcome. If you contribute, please keep changes focused and run the test suite before opening a pull request.
