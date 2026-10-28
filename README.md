# Observer

**A modular Discord bot for moderation, server safety, and community management.**

Observer brings server tools and a web dashboard together: configure your server, review reports, and manage day-to-day community features through Discord and the browser.

[Website & dashboard](https://observer-dashboard.observer/) · [Source code](https://github.com/blurock2/Observer-cogs)

> Observer is actively developed. Features and configuration may change.

## What Observer does

- **Moderation:** moderation commands, records, cases, statistics, and audit logs.
- **Safety:** scam message and link protection, join-burst and honeypot protections, and image checks using OCR, QR decoding, and trained fingerprints.
- **Reports and support:** message reports, anonymous reporting, ticket panels, and staff workflows.
- **Community:** reaction roles, rules, tags, leveling, temporary voice channels, reminders, and other member utilities.
- **Configuration:** per-server settings through `/setup` in Discord and the web dashboard.
- **Web tools:** Discord sign-in, a server picker, configuration for server owners, moderation and report views for authorized moderators, and custom messages for users with access.

Available features depend on configuration, Discord permissions, and the version being run. The dashboard uses Discord sign-in; the options shown depend on your server access.

## Getting started

### Use the hosted bot

Visit the [Observer website](https://observer-dashboard.observer/) and sign in with Discord to find your eligible servers and add Observer. Once it joins, use `/setup` to configure the features you need. Grant only the Discord permissions required for the features you enable.

### Run your own instance

1. Create a bot application in the [Discord Developer Portal](https://discord.com/developers/applications). Enable the privileged **Server Members**, **Message Content**, and **Presence** intents required by this instance in the Bot settings. Invite it with appropriate bot permissions and application commands.
2. Clone the repository and create a Python virtual environment:

   ```bash
   git clone https://github.com/blurock2/Observer-cogs.git
   cd Observer-cogs
   python -m venv .venv
   ```

   Activate it with `source .venv/bin/activate` on Linux/macOS or `.venv\Scripts\activate` on Windows.

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

   Image scam scanning also uses the Tesseract application: install it separately and make sure `tesseract` is on your PATH.

4. Copy `.env.example` to `.env` and set `DISCORD_TOKEN` and `BOT_OWNER_ID`. Review the remaining example settings before enabling integrations. Keep tokens and secrets private and never commit your real `.env`.
5. Start the bot:

   ```bash
   python bot.py
   ```

   Configure the server with `/setup`. Command availability and Discord permissions may affect what you can use.

## Website and dashboard

The frontend lives in `activity/` and is served by the bot's aiohttp service. To build it:

```bash
cd activity
npm install
npm run build
```

For a hosted dashboard, configure `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and `PUBLIC_BASE_URL` in `.env`; add `{PUBLIC_BASE_URL}/auth/callback` to the Discord application's OAuth2 redirect URLs. For a VPS, the existing deployment guidance uses `ACTIVITY_HOST=127.0.0.1` and an HTTPS reverse proxy to port 8081. Use your own domain and credentials when self-hosting.

The web interface includes server configuration, report and moderation views, and a custom-message editor. Access depends on the user's server permissions. Some functionality also remains available through Discord commands. Website sessions expire and a bot restart signs users out.

## Project structure

- `bot.py` — bot startup, intents, and extension loading.
- `cogs/` — modular Discord features and setup.
- `database.py` and `data/` — SQLite persistence and runtime data.
- `activity/` and `activity_server.py` — web frontend and service.
- `tests/` — automated tests.
- `deploy/` — deployment files.

Keep runtime databases, backups, tokens, and other private data out of version control. Back up persistent data before deploying or migrating an instance. Some integrations and support-server settings are specific to the official installation and should be reviewed before self-hosting.

## Development

Install the dependencies from `requirements.txt`, then run:

```bash
pytest
ruff check .
```

Cogs are discord.py extensions. Keep new features modular, preserve per-server settings, and add or update tests where appropriate. Issues and pull requests are welcome.

## License

Observer is available under the custom [Observer Source License](LICENSE). It permits use, modification, and redistribution, including commercial use, subject to attribution and restrictions on malicious use. It is **source-available**, not an OSI-approved open-source license. See the license for the actual terms.

Created by [blurock2 (Rocky / Rockus26)](https://github.com/blurock2). Observer is an independent project and is not affiliated with or endorsed by Discord Inc.
