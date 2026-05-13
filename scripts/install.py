#!/usr/bin/env python3
"""Install codex-turn-sound by wiring Codex's native notify command."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("CODEX_TURN_SOUND_STATE_DIR", Path.home() / ".codex" / "codex-turn-sound")).expanduser()
INSTALL_ROOT = STATE_DIR / "app"
TARGET_NOTIFY = INSTALL_ROOT / "bin" / "codex-turn-sound"
DEFAULT_SOUND = INSTALL_ROOT / "assets" / "soft-chime.wav"
STATE_JSON = STATE_DIR / "state.json"
STATE_ZSH = STATE_DIR / "original-notify.zsh"
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


def write_notify(text: str, notify: list[str]) -> str:
    rendered = toml_array(notify)
    lines, start, stop, _ = find_root_notify(text)
    if start is not None and stop is not None:
        lines[start:stop] = [f"notify = {rendered}\n"]
        return "".join(lines)

    root_end = root_table_start(lines)
    insertion = [f"notify = {rendered}\n", "\n"]
    if root_end == 0:
        lines[0:0] = insertion
        return "".join(lines)
    if lines and not lines[root_end - 1].endswith("\n"):
        lines[root_end - 1] += "\n"
    lines[root_end:root_end] = insertion
    return "".join(lines)


def shell_array(items: list[str]) -> str:
    if not items:
        return "()"
    return "( " + " ".join(shlex.quote(item) for item in items) + " )"


def existing_state() -> dict[str, Any]:
    if not STATE_JSON.exists():
        return {}
    try:
        payload = json.loads(STATE_JSON.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_this_tool(command: list[str] | None) -> bool:
    if not command:
        return False
    state = existing_state()
    previous_installed = state.get("installed_notify")
    if isinstance(previous_installed, list) and command == previous_installed:
        return True
    try:
        path = Path(command[0]).expanduser().resolve()
    except OSError:
        return False
    return path == TARGET_NOTIFY.resolve()


def legacy_original_notify(command: list[str] | None) -> list[str] | None:
    if not command:
        return None
    path = Path(command[0]).expanduser()
    if path.name != "codex-turn-ended-sound.sh" or not path.exists():
        return None
    match = re.search(r'^ORIGINAL_NOTIFY="([^"]+)"', path.read_text(errors="ignore"), re.M)
    if not match:
        return None
    return [match.group(1), *command[1:]]


def choose_original_notify(current: list[str] | None) -> list[str]:
    state = existing_state()
    previous = state.get("original_notify")
    if is_this_tool(current) and isinstance(previous, list):
        return [str(item) for item in previous if isinstance(item, str)]

    legacy = legacy_original_notify(current)
    if legacy:
        return legacy

    return current or []


def validate_toml(path: Path) -> None:
    try:
        import tomllib
    except ModuleNotFoundError:
        return
    tomllib.loads(path.read_text())


def copy_runtime() -> None:
    source = SOURCE_ROOT.resolve()
    target = INSTALL_ROOT.resolve()
    if source == target:
        return

    tmp_target = STATE_DIR / f".app-tmp-{int(time.time())}-{os.getpid()}"
    if tmp_target.exists():
        shutil.rmtree(tmp_target)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".DS_Store", "__pycache__", "node_modules"}
            or name.endswith(".pyc")
        }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, tmp_target, ignore=ignore)
    if INSTALL_ROOT.exists():
        shutil.rmtree(INSTALL_ROOT)
    tmp_target.rename(INSTALL_ROOT)

    for path in (
        INSTALL_ROOT / "bin" / "codex-turn-sound",
        INSTALL_ROOT / "install.sh",
        INSTALL_ROOT / "uninstall.sh",
        INSTALL_ROOT / "scripts" / "install.py",
        INSTALL_ROOT / "scripts" / "uninstall.py",
        INSTALL_ROOT / "scripts" / "make_sound.py",
    ):
        if path.exists():
            path.chmod(path.stat().st_mode | 0o755)


def ensure_sound(sound_path: Path) -> None:
    if sound_path.exists():
        return
    if sound_path != DEFAULT_SOUND:
        raise FileNotFoundError(f"Sound file not found: {sound_path}")
    subprocess.run(
        [sys.executable, str(INSTALL_ROOT / "scripts" / "make_sound.py"), str(DEFAULT_SOUND)],
        check=True,
    )


def install(config_path: Path, sound_path: Path) -> None:
    copy_runtime()
    ensure_sound(sound_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = config_path.read_text() if config_path.exists() else ""
    current_notify = parse_notify(text)
    original_notify = choose_original_notify(current_notify)

    backup_path = None
    if config_path.exists():
        backup_path = config_path.with_suffix(
            config_path.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(config_path, backup_path)

    new_notify = [str(TARGET_NOTIFY), "turn-ended"]
    try:
        config_path.write_text(write_notify(text, new_notify))
        validate_toml(config_path)
    except Exception:
        config_path.write_text(text)
        raise

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(
        json.dumps(
            {
                "installed_notify": new_notify,
                "original_notify": original_notify,
                "sound_path": str(sound_path),
            },
            indent=2,
        )
        + "\n"
    )
    STATE_ZSH.write_text(
        "# Generated by codex-turn-sound. Do not edit while installed.\n"
        "typeset -ga ORIGINAL_NOTIFY\n"
        f"ORIGINAL_NOTIFY={shell_array(original_notify)}\n"
        f"TURN_SOUND_PATH={shlex.quote(str(sound_path))}\n"
    )

    print(f"installed_notify={toml_array(new_notify)}")
    print(f"sound={sound_path}")
    if backup_path:
        print(f"backup={backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path.home() / ".codex" / "config.toml"))
    parser.add_argument("--sound", default=str(DEFAULT_SOUND))
    args = parser.parse_args()
    install(Path(args.config).expanduser().resolve(), Path(args.sound).expanduser().resolve())


if __name__ == "__main__":
    main()
