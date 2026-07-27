#!/usr/bin/env python3
"""Diagnose the installed notification chain without exposing event payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from common import (
    PACKAGE_VERSION,
    StateError,
    build_paths,
    command_contains_tool,
    load_state,
    validate_state_for_paths,
)
from config_toml import ConfigError, read_notify


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagnose(play: bool = False) -> dict:
    paths = build_paths(SOURCE_ROOT)
    issues: list[str] = []
    state = None
    try:
        state = load_state(paths.state_json)
        validate_state_for_paths(state, paths)
    except StateError as error:
        issues.append(str(error))
        state = None

    config_path = paths.codex_home / "config.toml"
    current = None
    if state is not None:
        config_path = Path(state.get("config_path", config_path)).expanduser().resolve()
        try:
            text = config_path.read_text(encoding="utf-8")
            current = read_notify(text)
        except (OSError, ConfigError) as error:
            issues.append(f"config: {error}")
        try:
            contains_installation = command_contains_tool(
                current, state["installed_notify"], paths.target_notify
            )
        except StateError as error:
            issues.append(f"config notify chain: {error}")
        else:
            if not contains_installation:
                issues.append("config notify chain does not contain this installation")

    runtime = paths.target_notify
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        issues.append(f"runtime is missing or not executable: {runtime}")

    installed_version = None
    package_file = paths.install_root / "package.json"
    if package_file.is_file():
        try:
            package_payload = json.loads(package_file.read_text())
            if isinstance(package_payload, dict):
                installed_version = package_payload.get("version")
            else:
                issues.append("installed package.json must contain an object")
        except (json.JSONDecodeError, OSError):
            issues.append("installed package.json is unreadable")
    if installed_version != PACKAGE_VERSION:
        issues.append(
            f"runtime version mismatch: installed={installed_version}, expected={PACKAGE_VERSION}"
        )

    sound_path = Path(state["sound_path"]) if state is not None else None
    if sound_path is None or not sound_path.is_file():
        issues.append(f"configured sound is missing: {sound_path}")
    elif state is not None:
        actual_hash = sha256(sound_path)
        expected_hash = state.get("sound_sha256")
        if expected_hash and actual_hash != expected_hash:
            issues.append("configured sound checksum does not match state.json")
        result = subprocess.run(
            ["/usr/bin/afinfo", str(sound_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            issues.append("configured sound is not accepted by afinfo")
        elif play:
            subprocess.run(["/usr/bin/afplay", str(sound_path)], check=False)

    return {
        "ok": not issues,
        "package_version": PACKAGE_VERSION,
        "installed_version": installed_version,
        "config_path": str(config_path),
        "state_path": str(paths.state_json),
        "sound_path": str(sound_path) if sound_path else None,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()
    try:
        result = diagnose(args.play)
    except Exception as error:
        result = {
            "ok": False,
            "package_version": PACKAGE_VERSION,
            "installed_version": None,
            "config_path": None,
            "state_path": None,
            "sound_path": None,
            "issues": [f"unexpected diagnostic failure: {error}"],
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print("codex-turn-sound: healthy")
        print(f"version={result['installed_version']}")
        print(f"sound={result['sound_path']}")
    else:
        print("codex-turn-sound: problems found")
        for issue in result["issues"]:
            print(f"- {issue}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
