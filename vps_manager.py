from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent
DATA = PROJECT / "data"
DB = DATA / "bot.db"
GUILDS = DATA / "app_guilds.json"
SERVICE = "observer"


def response(data: Any = None, error: str | None = None) -> None:
    print(json.dumps({"ok": error is None, "data": data, "error": error}))


def systemctl(action: str) -> None:
    allowed = {"start", "stop", "restart"}
    if action not in allowed:
        raise ValueError("Unsupported service action")
    subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", action, SERVICE],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def status() -> dict[str, Any]:
    active = subprocess.run(
        ["/usr/bin/systemctl", "is-active", SERVICE],
        capture_output=True,
        text=True,
    ).stdout.strip() == "active"
    live_data: dict[str, Any] = {}
    if GUILDS.is_file():
        try:
            loaded = json.loads(GUILDS.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                live_data = loaded
        except (OSError, json.JSONDecodeError):
            live_data = {}

    guilds = live_data.get("guilds", [])
    connected = active
    return {
        "running": active,
        "connected": connected,
        "guild_count": len(guilds) if isinstance(guilds, list) else 0,
        "started_at": live_data.get("started_at"),
        "latency_ms": live_data.get("latency_ms"),
    }


def logs(lines: int) -> list[str]:
    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/journalctl", "-u", SERVICE, "-n", str(min(max(lines, 1), 500)), "--no-pager", "-o", "cat"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not read the Observer journal.")
    return result.stdout.splitlines()


def set_module(guild_id: int, module: str, enabled: bool) -> None:
    with sqlite3.connect(DB, timeout=10) as connection:
        connection.execute(
            """INSERT INTO guild_setup_config (guild_id, module, key, value)
               VALUES (?, ?, 'enabled', ?)
               ON CONFLICT(guild_id, module, key) DO UPDATE SET value=excluded.value""",
            (guild_id, module, json.dumps(bool(enabled))),
        )


def get_configured_guilds() -> list[int]:
    with sqlite3.connect(DB, timeout=10) as connection:
        rows = connection.execute(
            "SELECT DISTINCT guild_id FROM guild_setup_config ORDER BY guild_id"
        ).fetchall()
    return [int(row[0]) for row in rows]


def get_module(guild_id: int, module: str) -> dict[str, Any]:
    with sqlite3.connect(DB, timeout=10) as connection:
        rows = connection.execute(
            "SELECT key, value FROM guild_setup_config WHERE guild_id=? AND module=?",
            (guild_id, module),
        ).fetchall()
    values: dict[str, Any] = {}
    for key, value in rows:
        try:
            values[str(key)] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            values[str(key)] = value
    return values


def config_snapshot() -> dict[str, Any]:
    with sqlite3.connect(DB, timeout=10) as connection:
        rows = connection.execute(
            "SELECT guild_id, module, key, value FROM guild_setup_config"
        ).fetchall()
    modules: dict[str, dict[str, dict[str, Any]]] = {}
    guild_ids: set[int] = set()
    for guild_id, module, key, value in rows:
        guild_key = str(guild_id)
        guild_ids.add(int(guild_id))
        module_values = modules.setdefault(guild_key, {}).setdefault(str(module), {})
        try:
            module_values[str(key)] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            module_values[str(key)] = value
    return {"guild_ids": sorted(guild_ids), "modules": modules}


def message_result(request_id: str) -> dict[str, Any] | None:
    result_file = DATA / "app_message_results.json"
    if not result_file.is_file():
        return None
    results = json.loads(result_file.read_text(encoding="utf-8"))
    return next(
        (item for item in reversed(results) if item.get("id") == request_id),
        None,
    )


def main() -> None:
    action = sys.argv[1]
    payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if action in {"start", "stop", "restart"}:
        systemctl(action)
        response({"action": action})
    elif action == "status":
        response(status())
    elif action == "logs":
        response(logs(int(payload.get("lines", 120))))
    elif action == "guilds":
        response(json.loads(GUILDS.read_text(encoding="utf-8")) if GUILDS.is_file() else {})
    elif action == "config_guilds":
        response(get_configured_guilds())
    elif action == "config_snapshot":
        response(config_snapshot())
    elif action == "get_module":
        response(get_module(int(payload["guild_id"]), str(payload["module"])))
    elif action == "set_module":
        set_module(int(payload["guild_id"]), str(payload["module"]), bool(payload["enabled"]))
        response({"updated": True})
    elif action == "send_message":
        request_file = DATA / "app_message_requests.json"
        requests = json.loads(request_file.read_text(encoding="utf-8")) if request_file.is_file() else []
        request_id = payload.get("id") or __import__("uuid").uuid4().hex
        requests.append({"id": request_id, "channel_id": int(payload["channel_id"]), "content": str(payload["content"])})
        request_file.write_text(json.dumps(requests, indent=2), encoding="utf-8")
        response({"id": request_id})
    elif action == "message_result":
        response(message_result(str(payload["id"])))
    else:
        raise ValueError(f"Unknown action: {action}")


try:
    main()
except Exception as error:
    response(error=f"{type(error).__name__}: {error}")