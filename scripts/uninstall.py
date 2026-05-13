#!/usr/bin/env python3
"""Restore the previous Codex notify command."""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_NOTIFY = REPO_ROOT / "bin" / "codex-turn-sound"
STATE_JSON = Path.home() / ".codex" / "codex-turn-sound" / "state.json"
NOTIFY_RE = re.compile(r"(?m)^(?P<prefix>\s*notify\s*=\s*)(?P<value>\[[^\n]*\])(?P<suffix>\s*(?:#.*)?$)")


def parse_notify(text: str) -> list[str] | None:
    match = NOTIFY_RE.search(text)
    if not match:
        return None
    value = ast.literal_eval(match.group("value"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("notify must be a string array")
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
    return path == TARGET_NOTIFY.resolve() or path.name == "codex-turn-sound"


def replace_or_remove_notify(text: str, original_notify: list[str]) -> str:
    if original_notify:
        return NOTIFY_RE.sub(
            lambda match: f"{match.group('prefix')}{toml_array(original_notify)}{match.group('suffix')}",
            text,
            count=1,
        )
    return NOTIFY_RE.sub("", text, count=1)


def uninstall(config_path: Path) -> None:
    if not config_path.exists():
        print(f"config not found: {config_path}")
        return

    text = config_path.read_text()
    current_notify = parse_notify(text)
    if not is_this_tool(current_notify):
        print("current notify is not codex-turn-sound; nothing changed")
        return

    state = json.loads(STATE_JSON.read_text()) if STATE_JSON.exists() else {}
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
