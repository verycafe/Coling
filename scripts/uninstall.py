#!/usr/bin/env python3
"""Restore the previous Codex notify command."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import time
from pathlib import Path


STATE_DIR = Path(os.environ.get("CODEX_TURN_SOUND_STATE_DIR", Path.home() / ".codex" / "codex-turn-sound")).expanduser()
INSTALL_ROOT = STATE_DIR / "app"
TARGET_NOTIFY = INSTALL_ROOT / "bin" / "codex-turn-sound"
STATE_JSON = STATE_DIR / "state.json"
ROOT_NOTIFY_RE = re.compile(r"^\s*notify\s*=")
TABLE_HEADER_RE = re.compile(r"^\s*\[")


def parse_notify_value(value_text: str) -> list[str]:
    value = ast.literal_eval(value_text)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("notify must be a string array")
    return value


def root_table_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if TABLE_HEADER_RE.match(line):
            return index
    return len(lines)


def find_root_notify(text: str) -> tuple[list[str], int | None, int | None, list[str] | None]:
    lines = text.splitlines(keepends=True)
    root_end = root_table_start(lines)
    for start in range(root_end):
        if not ROOT_NOTIFY_RE.match(lines[start]):
            continue
        for stop in range(start + 1, root_end + 1):
            candidate = "".join(lines[start:stop])
            value_text = candidate.split("=", 1)[1]
            try:
                value = parse_notify_value(value_text)
            except (SyntaxError, ValueError):
                continue
            return lines, start, stop, value
        raise ValueError("root notify exists but is not a string array")
    return lines, None, None, None


def parse_notify(text: str) -> list[str] | None:
    _, _, _, value = find_root_notify(text)
    return value


def toml_array(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(item) for item in items) + "]"


def is_this_tool(command: list[str] | None) -> bool:
    if not command:
        return False
    if STATE_JSON.exists():
        try:
            state = json.loads(STATE_JSON.read_text())
        except json.JSONDecodeError:
            state = {}
        installed = state.get("installed_notify") if isinstance(state, dict) else None
        if isinstance(installed, list) and command == installed:
            return True
    try:
        path = Path(command[0]).expanduser().resolve()
    except OSError:
        return False
    return path == TARGET_NOTIFY.resolve()


def replace_or_remove_notify(text: str, original_notify: list[str]) -> str:
    lines, start, stop, _ = find_root_notify(text)
    if start is None or stop is None:
        return text
    if original_notify:
        lines[start:stop] = [f"notify = {toml_array(original_notify)}\n"]
    else:
        lines[start:stop] = []
    return "".join(lines)


def load_state() -> dict:
    if not STATE_JSON.exists():
        return {}
    try:
        payload = json.loads(STATE_JSON.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def uninstall(config_path: Path) -> None:
    if not config_path.exists():
        print(f"config not found: {config_path}")
        return

    text = config_path.read_text()
    current_notify = parse_notify(text)
    if not is_this_tool(current_notify):
        print("current notify is not codex-turn-sound; nothing changed")
        return

    state = load_state()
    original_notify = state.get("original_notify", [])
    if not isinstance(original_notify, list):
        original_notify = []
    original_notify = [str(item) for item in original_notify if isinstance(item, str)]

    backup_path = config_path.with_suffix(
        config_path.suffix + f".bak-uninstall-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(config_path, backup_path)
    config_path.write_text(replace_or_remove_notify(text, original_notify))
    print(f"restored_notify={toml_array(original_notify) if original_notify else '<removed>'}")
    print(f"backup={backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path.home() / ".codex" / "config.toml"))
    args = parser.parse_args()
    uninstall(Path(args.config).expanduser().resolve())


if __name__ == "__main__":
    main()
