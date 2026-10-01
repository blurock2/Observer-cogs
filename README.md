# Observer

Observer is a modular Discord bot built with Python and [`discord.py`](https://discordpy.readthedocs.io/). It provides server management, moderation, security, community, utility, and automation features through independently loadable cogs.

> **Project status:** Active development. Core functionality is implemented and tested, but commands, features, and configuration may continue to change.

## Features

Observer currently includes:

- **Server configuration**
  - Persistent per-server configuration
  - In-Discord setup interface
  - Configurable staff and tester roles
  - Configurable bot access restrictions

- **Moderation**
  - Moderation commands
  - Moderation statistics
  - Persistent moderation action records
  - Moderation logging
  - Undo/reversal support where available

- **Security & auditing**
  - Audit logging
  - Security-related server tools
  - Configurable command access
  - Role/permission-aware moderation controls

- **Community tools**
  - Reaction roles
  - Rules
  - Mentions
  - Member commands
  - Tags
  - Leveling
  - Temporary voice channels

- **Tickets & reports**
  - Ticket system
  - Message reporting
  - Configurable ticket/report channels and roles

- **Messaging**
  - Message quoting
  - Message relaying
  - Cross-channel/server relay functionality

- **Utilities**
  - Reminders
  - Server statistics
  - Weather information
  - Spotify display features
  - In-Discord help

- **Integrations**
  - Account linking
  - Application bridge
  - External-service integrations where configured

Features are implemented as Discord.py extensions in the `cogs/` directory. This keeps individual features separated and allows them to be loaded, reloaded, and maintained independently.

## Project Structure

```text
Observer-cogs/
├── bot.py                 # Application entry point and bot lifecycle
├── database.py            # SQLite database, migrations, backups and helpers
├── logging_config.py       # Logging configuration
├── vps_manager.py         # VPS/deployment management utility
├── cogs/
│   ├── setup_ui.py        # Server setup and configuration
│   ├── audit_log.py       # Audit logging
│   ├── acc_link.py        # Account linking
│   ├── reaction_roles.py  # Reaction roles
│   ├── message_quoter.py  # Message quoting
│   ├── utilities.py       # General utilities
│   ├── report_msg.py      # Message reporting
│   ├── server_stats.py    # Server statistics
│   ├── temporary_voice.py # Temporary voice channels
│   ├── rules.py           # Server rules
│   ├── tickets.py         # Ticket system
│   ├── reminder.py        # Reminders
│   ├── message_relay.py   # Message relay
│   ├── moderation.py      # Moderation
│   ├── security.py        # Security features
│   ├── tags.py            # Server tags
│   ├── mentions.py        # Mention-related tools
│   ├── member_commands.py # Member commands
│   ├── help.py            # In-Discord help
│   ├── leveling.py        # Leveling system
│   ├── weather.py         # Weather features
│   ├── spotify_show.py    # Spotify display features
│   └── app_bridge.py      # Application bridge
├── tests/                 # Automated test suite
├── deploy/                # Deployment/systemd files
├── data/                  # Local runtime database/backups
├── .env.example           # Environment variable template
├── .gitignore             # Git exclusions
├── requirements.txt       # Python dependencies
└── VERSION                # Project version
