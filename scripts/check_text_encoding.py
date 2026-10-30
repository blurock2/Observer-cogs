#!/usr/bin/env python3
"""Fail when source files contain common UTF-8/Windows-1252 mojibake."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py", ".js", ".css", ".html", ".md", ".txt", ".yml", ".yaml",
    ".json", ".toml", ".sh",
}
EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "dist", "__pycache__",
}

# Construct markers from code points so this guard doesn't contain the damaged
# spellings it is meant to catch.
MARKERS = (
    "\u00c3",                 # e.g. UTF-8 bytes read as a single-byte charset
    "\u00c2",                 # e.g. non-breaking spaces decoded incorrectly
    "\u00e2",                 # broad marker for corrupted UTF-8 punctuation
    "\u00e2\u0099",           # common emoji sequences
    "\u00e2\u009d",           # common emoji sequences
    "\u00f0\u0178",           # emoji decoded through Windows-1252
    "\u00ef\u00bb\u00bf",   # UTF-8 BOM rendered as text
    "\ufffd",                 # replacement character from lossy decoding
)


def tracked_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        names = result.stdout.decode("utf-8").split("\0")
        files = [ROOT / name for name in names if name]
    except (OSError, subprocess.CalledProcessError):
        files = list(ROOT.rglob("*"))

    return [
        path
        for path in files
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part in EXCLUDED_PARTS for part in path.parts)
    ]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            failures.append(f"{path.relative_to(ROOT)}: not valid UTF-8 ({error})")
            continue
        except OSError as error:
            failures.append(f"{path.relative_to(ROOT)}: could not read ({error})")
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            marker = next((item for item in MARKERS if item in line), None)
            has_c1_control = any(0x80 <= ord(char) <= 0x9F for char in line)
            if marker or has_c1_control:
                failures.append(
                    f"{path.relative_to(ROOT)}:{line_number}: suspicious encoding marker"
                )

    if failures:
        print("Text encoding check failed:")
        print("\n".join(f"  {failure}" for failure in failures))
        print("Save source files as UTF-8 and restore any garbled punctuation or emoji.")
        return 1

    print("Text encoding check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

