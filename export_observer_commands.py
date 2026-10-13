#!/usr/bin/env python3
"""Export Observer's actual Discord application commands to importable JSON.

Requires: Python 3.10+, OBSERVER_BOT_TOKEN environment variable.
Optional: OBSERVER_APPLICATION_ID and OBSERVER_GUILD_ID environment variables.
No third-party packages.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = 'https://discord.com/api/v10'
# Writable definition fields understood by Discord's application command API.
FIELDS = (
    'type', 'name', 'name_localizations', 'description', 'description_localizations',
    'options', 'default_member_permissions', 'dm_permission', 'nsfw',
    'integration_types', 'contexts',
)


def get_json(path: str, token: str):
    request = urllib.request.Request(
        API + path,
        headers={
            'Authorization': f'Bot {token}',
            'User-Agent': 'ObserverCommandExporter/1.0',
            'Accept': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        message = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Discord API returned HTTP {exc.code}: {message}') from exc


def main() -> int:
    token = os.getenv('OBSERVER_BOT_TOKEN', '').strip()
    if not token:
        print('Missing OBSERVER_BOT_TOKEN environment variable.', file=sys.stderr)
        return 2

    app_id = os.getenv('OBSERVER_APPLICATION_ID', '').strip()
    if not app_id:
        app_id = str(get_json('/oauth2/applications/@me', token)['id'])
    if not app_id.isdigit():
        raise ValueError('OBSERVER_APPLICATION_ID must be numeric.')

    guild_id = os.getenv('OBSERVER_GUILD_ID', '').strip()
    if guild_id and not guild_id.isdigit():
        raise ValueError('OBSERVER_GUILD_ID must be numeric.')

    endpoint = f'/applications/{app_id}/commands'
    if guild_id:
        endpoint = f'/applications/{app_id}/guilds/{guild_id}/commands'
    commands = get_json(endpoint, token)

    # Remove read-only fields (IDs, versions, application association, etc.).
    # Keep native types and exact nested options/choices/permissions.
    definitions = [
        {key: command[key] for key in FIELDS if key in command}
        for command in commands
    ]
    definitions.sort(key=lambda item: (item.get('type', 1), item['name']))
    outfile = Path('observer_discord_import.json')
    outfile.write_text(json.dumps(definitions, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    slash = sum(c.get('type', 1) == 1 for c in definitions)
    context = len(definitions) - slash
    print(f'Exported {slash} slash commands and {context} context-menu commands to {outfile.resolve()}')
    print('Prefix commands (!) are not Discord application commands and cannot be included.')
    print('This exports the selected command scope only (global by default, guild if OBSERVER_GUILD_ID is set).')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, urllib.error.URLError) as error:
        print(f'Export failed: {error}', file=sys.stderr)
        raise SystemExit(1)
