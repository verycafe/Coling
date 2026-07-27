#!/usr/bin/env python3
"""Install codex-turn-sound through Codex's native notify command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from common import (
    PACKAGE_VERSION,
    STATE_SCHEMA_VERSION,
    Paths,
    StateError,
    atomic_write_text,
    build_paths,
    command_contains_tool,
    command_chain_looks_like_tool,
    command_is_tool,
    ensure_private_directory,
    ensure_managed_file,
    installation_lock,
    load_state,
    restore_snapshot,
    rewrite_nested_tool,
    snapshot,
    validate_config_path,
    validate_runtime_paths,
    validate_state_for_paths,
)
from config_toml import ConfigError, read_notify, render_array, set_notify


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def unique_suffix() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{timestamp}-{secrets.token_hex(4)}"


def validate_platform() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("codex-turn-sound currently supports macOS only")
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    for executable in (Path("/bin/zsh"), Path("/usr/bin/afplay"), Path("/usr/bin/afinfo")):
        if not executable.exists():
            raise RuntimeError(f"Required macOS executable not found: {executable}")


def validate_sound(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Sound file is not a regular file: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"Sound file is not readable: {path}")
    result = subprocess.run(
        ["/usr/bin/afinfo", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unsupported or damaged audio"
        raise ValueError(f"Sound file failed validation: {detail}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_runtime(
    paths: Paths, custom_sound: Path | None
) -> tuple[Path, Path, str, str]:
    validate_runtime_paths(paths)
    stage = paths.state_dir / f".app-stage-{unique_suffix()}"

    def ignore(_directory: str, names: list[str]) -> set[str]:
        excluded = {
            ".git",
            ".github",
            ".gitignore",
            ".npmrc",
            ".DS_Store",
            "__pycache__",
            "node_modules",
            "tests",
        }
        return {
            name
            for name in names
            if name in excluded or name.endswith((".pyc", ".tgz"))
        }

    try:
        shutil.copytree(paths.source_root, stage, ignore=ignore)
        for relative in (
            "bin/codex-turn-sound",
            "install.sh",
            "uninstall.sh",
            "scripts/install.py",
            "scripts/uninstall.py",
            "scripts/make_sound.py",
            "scripts/config_toml.py",
            "scripts/common.py",
            "scripts/notify_runtime.py",
            "scripts/doctor.py",
        ):
            executable = stage / relative
            if executable.exists():
                executable.chmod(executable.stat().st_mode | 0o755)

        if custom_sound is None:
            staged_sound = stage / "assets" / "soft-chime.wav"
            validate_sound(staged_sound)
            installed_sound = paths.install_root / "assets" / "soft-chime.wav"
            sound_kind = "bundled"
        else:
            validate_sound(custom_sound)
            suffix = custom_sound.suffix.lower() or ".audio"
            staged_sound = stage / "assets" / f"custom-sound{suffix}"
            shutil.copy2(custom_sound, staged_sound)
            staged_sound.chmod(0o600)
            installed_sound = paths.install_root / "assets" / staged_sound.name
            sound_kind = "custom"
        return stage, installed_sound, sound_kind, file_sha256(staged_sound)
    except Exception as error:
        try:
            if stage.exists():
                shutil.rmtree(stage)
        except Exception as cleanup_error:
            raise ExceptionGroup(
                "runtime staging failed and cleanup was incomplete",
                [error, cleanup_error],
            )
        raise


def effective_original_notify(
    current: list[str] | None,
    state: dict | None,
    paths: Paths,
    config_path: Path,
) -> tuple[list[str], bool]:
    installed = (
        state["installed_notify"]
        if state is not None
        else [str(paths.target_notify), "turn-ended"]
    )
    current_has_tool = command_contains_tool(current, installed, paths.target_notify)

    if state and state.get("schema_version") == STATE_SCHEMA_VERSION:
        previous_config = Path(state["config_path"]).expanduser().resolve()
        if previous_config != config_path:
            if current_has_tool:
                raise StateError(
                    f"state.json belongs to another config: {previous_config}"
                )
            previous_text = (
                previous_config.read_text(encoding="utf-8")
                if previous_config.exists()
                else ""
            )
            previous_notify = read_notify(previous_text)
            if command_contains_tool(
                previous_notify, state["installed_notify"], paths.target_notify
            ):
                raise StateError(
                    f"codex-turn-sound is still installed in another config: {previous_config}"
                )

    if current_has_tool:
        if state is None:
            raise StateError(
                "Codex config points to codex-turn-sound but state.json is missing; "
                "restore a config backup before reinstalling"
            )
        original = state["original_notify"]
        if command_contains_tool(
            original, installed, paths.target_notify
        ) or command_chain_looks_like_tool(original):
            raise StateError("state.json would make codex-turn-sound invoke itself")
        original_present = state.get("original_notify_present", bool(original))
        if command_is_tool(current, installed, paths.target_notify):
            return list(original), original_present
        rewritten, changed = rewrite_nested_tool(
            current or [], installed, paths.target_notify, original
        )
        if not changed:
            raise StateError("Could not safely unwrap the existing notify chain")
        return rewritten, True

    if command_chain_looks_like_tool(current):
        raise StateError(
            "Codex config contains an unrecognized codex-turn-sound path; "
            "restore or remove it before installing"
        )

    return list(current or []), current is not None


def preserved_custom_sound(
    state: dict | None,
    current: list[str] | None,
    paths: Paths,
    reset_sound: bool,
) -> Path | None:
    if reset_sound or state is None:
        return None
    if not command_contains_tool(
        current, state["installed_notify"], paths.target_notify
    ):
        return None
    sound_kind = state.get("sound_kind")
    sound_path = Path(state["sound_path"]).expanduser().resolve()
    is_custom = sound_kind == "custom" or (
        sound_kind is None and sound_path.name != "soft-chime.wav"
    )
    if not is_custom:
        return None
    validate_sound(sound_path)
    expected_hash = state.get("sound_sha256")
    if expected_hash and file_sha256(sound_path) != expected_hash:
        raise StateError(
            "managed custom sound checksum changed; pass --sound explicitly "
            "to accept a replacement"
        )
    return sound_path


def _swap_runtime(paths: Paths, stage: Path) -> Path | None:
    rollback = None
    try:
        if paths.install_root.exists():
            rollback = paths.state_dir / f".app-rollback-{unique_suffix()}"
            os.replace(paths.install_root, rollback)
        os.replace(stage, paths.install_root)
    except Exception:
        if rollback and rollback.exists() and not paths.install_root.exists():
            os.replace(rollback, paths.install_root)
        raise
    return rollback


def _restore_runtime(paths: Paths, rollback: Path | None) -> None:
    if paths.install_root.exists():
        shutil.rmtree(paths.install_root)
    if rollback and rollback.exists():
        os.replace(rollback, paths.install_root)


def install(
    config_path: Path,
    custom_sound: Path | None = None,
    reset_sound: bool = False,
) -> None:
    validate_platform()
    paths = build_paths(SOURCE_ROOT)
    config_path = config_path.expanduser().resolve()
    custom_sound = custom_sound.expanduser().resolve() if custom_sound else None
    if custom_sound is not None and reset_sound:
        raise ValueError("--sound and --reset-sound cannot be used together")
    validate_config_path(config_path, paths)

    with installation_lock(paths):
        ensure_private_directory(paths.state_dir)
        ensure_managed_file(paths.state_json)
        ensure_managed_file(paths.state_zsh)
        state_before = snapshot(paths.state_json)
        zsh_before = snapshot(paths.state_zsh)
        state = load_state(paths.state_json)
        validate_state_for_paths(state, paths)

        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_before = snapshot(config_path)
        config_text = config_before.content.decode("utf-8") if config_before.exists else ""
        current_notify = read_notify(config_text)
        original_notify, original_notify_present = effective_original_notify(
            current_notify, state, paths, config_path
        )
        selected_sound = custom_sound or preserved_custom_sound(
            state, current_notify, paths, reset_sound
        )

        stage = None
        rollback = None
        runtime_swapped = False
        backup_path = None
        try:
            stage, installed_sound, sound_kind, sound_sha256 = stage_runtime(
                paths, selected_sound
            )
            new_notify = [str(paths.target_notify), "turn-ended"]
            new_config = set_notify(config_text, new_notify)

            if config_before.exists:
                backup_path = config_path.with_name(
                    f"{config_path.name}.bak-install-{unique_suffix()}"
                )
                shutil.copy2(config_path, backup_path)

            if snapshot(config_path) != config_before:
                raise StateError("config.toml changed while installation was being prepared")

            state_payload = {
                "schema_version": STATE_SCHEMA_VERSION,
                "package_version": PACKAGE_VERSION,
                "config_path": str(config_path),
                "installed_notify": new_notify,
                "original_notify": original_notify,
                "original_notify_present": original_notify_present,
                "sound_path": str(installed_sound),
                "sound_kind": sound_kind,
                "sound_sha256": sound_sha256,
                "config_backup": str(backup_path) if backup_path else None,
            }

            atomic_write_text(
                paths.state_json,
                json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n",
                0o600,
            )
            rollback = _swap_runtime(paths, stage)
            runtime_swapped = True
            stage = None

            if snapshot(config_path) != config_before:
                raise StateError("config.toml changed before the installation could commit")
            atomic_write_text(
                config_path,
                new_config,
                config_before.mode if config_before.exists else 0o600,
            )
            paths.state_zsh.unlink(missing_ok=True)
        except Exception as error:
            rollback_errors: list[Exception] = []
            actions = [
                lambda: restore_snapshot(config_path, config_before),
                lambda: restore_snapshot(paths.state_json, state_before),
                lambda: restore_snapshot(paths.state_zsh, zsh_before),
            ]
            if runtime_swapped:
                actions.append(lambda: _restore_runtime(paths, rollback))
            if stage and stage.exists():
                actions.append(lambda: shutil.rmtree(stage))
            if backup_path:
                actions.append(lambda: backup_path.unlink(missing_ok=True))
            for action in actions:
                try:
                    action()
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                raise ExceptionGroup(
                    "installation failed and rollback was incomplete",
                    [error, *rollback_errors],
                )
            raise
        else:
            if rollback and rollback.exists():
                shutil.rmtree(rollback)

    print(f"installed_notify={render_array(new_notify)}")
    print(f"sound={installed_sound}")
    if backup_path:
        print(f"backup={backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--sound")
    parser.add_argument("--reset-sound", action="store_true")
    args = parser.parse_args()
    paths = build_paths(SOURCE_ROOT)
    config = Path(args.config) if args.config else paths.codex_home / "config.toml"
    sound = Path(args.sound) if args.sound else None
    try:
        install(config, sound, args.reset_sound)
    except (ConfigError, StateError, OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"codex-turn-sound: {error}\n")


if __name__ == "__main__":
    main()
