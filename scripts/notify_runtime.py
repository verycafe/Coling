#!/usr/bin/env python3
"""Execute the turn notification without evaluating persisted shell code."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from common import (
    StateError,
    build_paths,
    command_contains_tool,
    load_state,
    validate_state_for_paths,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def detached(command: list[str], environment: dict[str, str]) -> None:
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )


def resolve_sound(value: str, bundled: Path) -> Path | None:
    if value == "off":
        return None
    candidate = Path(value).expanduser()
    if "/" not in value and "." not in value:
        system = Path("/System/Library/Sounds") / f"{value}.aiff"
        if system.is_file():
            return system
    if candidate.is_file():
        return candidate.resolve()
    return bundled if bundled.is_file() else None


def notify(arguments: list[str]) -> int:
    if os.environ.get("CODEX_TURN_SOUND_ACTIVE") == "1":
        return 0

    event = arguments[0] if arguments else "turn-ended"
    payload_arguments = arguments[1:]
    paths = build_paths(SOURCE_ROOT)
    bundled = SOURCE_ROOT / "assets" / "soft-chime.wav"
    state = None
    try:
        state = load_state(paths.state_json)
        validate_state_for_paths(state, paths)
    except StateError:
        state = None

    environment = os.environ.copy()
    environment["CODEX_TURN_SOUND_ACTIVE"] = "1"

    if state is not None:
        original = state["original_notify"]
        installed = state["installed_notify"]
        if original and not command_contains_tool(
            original, installed, paths.target_notify
        ):
            try:
                detached([*original, *payload_arguments], environment)
            except OSError:
                pass

    if event != "turn-ended":
        return 0

    configured = os.environ.get(
        "CODEX_TURN_SOUND",
        state["sound_path"] if state is not None else str(bundled),
    )
    sound = resolve_sound(configured, bundled)
    if sound is not None and Path("/usr/bin/afplay").is_file():
        try:
            detached(["/usr/bin/afplay", str(sound)], environment)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(notify(sys.argv[1:]))
