#!/usr/bin/env python3
"""Restore the previous Codex notify command and remove installed runtime files."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import (
    STATE_SCHEMA_VERSION,
    StateError,
    atomic_write_text,
    build_paths,
    command_is_tool,
    command_contains_tool,
    command_chain_looks_like_tool,
    installation_lock,
    load_state,
    rewrite_nested_tool,
    snapshot,
    validate_config_path,
    validate_runtime_paths,
    validate_state_for_paths,
)
from config_toml import ConfigError, read_notify, remove_notify, render_array, set_notify


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def unique_backup(config_path: Path) -> Path:
    from install import unique_suffix

    return config_path.with_name(
        f"{config_path.name}.bak-uninstall-{unique_suffix()}"
    )


def quarantine_managed_files(paths) -> list[tuple[Path, Path]]:
    from install import unique_suffix

    suffix = unique_suffix()
    pairs: list[tuple[Path, Path]] = []
    candidates = (
        (paths.install_root, paths.state_dir / f".uninstall-{suffix}-app"),
        (paths.state_json, paths.state_dir / f".uninstall-{suffix}-state.json"),
        (paths.state_zsh, paths.state_dir / f".uninstall-{suffix}-state.zsh"),
    )
    try:
        for source, target in candidates:
            if source.exists():
                source.replace(target)
                pairs.append((source, target))
    except Exception as error:
        rollback_errors: list[Exception] = []
        for source, target in reversed(pairs):
            try:
                if target.exists():
                    target.replace(source)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise ExceptionGroup(
                "uninstall quarantine failed and rollback was incomplete",
                [error, *rollback_errors],
            )
        raise
    return pairs


def restore_quarantine(pairs: list[tuple[Path, Path]]) -> list[Exception]:
    errors: list[Exception] = []
    for source, target in reversed(pairs):
        try:
            if target.exists():
                target.replace(source)
        except Exception as error:
            errors.append(error)
    return errors


def delete_quarantine(pairs: list[tuple[Path, Path]]) -> list[str]:
    warnings: list[str] = []
    for _source, target in pairs:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError as error:
            warnings.append(f"could not remove quarantined file {target}: {error}")
    return warnings


def uninstall(config_argument: Path | None) -> None:
    paths = build_paths(SOURCE_ROOT)
    validate_runtime_paths(paths)
    not_installed = False
    cleanup_warnings: list[str] = []

    with installation_lock(paths):
        state = load_state(paths.state_json)
        validate_state_for_paths(state, paths)
        if state is None:
            config_path = (
                config_argument.expanduser().resolve()
                if config_argument
                else paths.codex_home / "config.toml"
            )
            text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
            current = read_notify(text)
            expected = [str(paths.target_notify), "turn-ended"]
            if command_chain_looks_like_tool(current) or command_contains_tool(
                current, expected, paths.target_notify
            ):
                raise StateError(
                    "Codex config points to codex-turn-sound but state.json is missing; "
                    "restore a config backup instead of removing notify blindly"
                )
            not_installed = True
            original = []
            backup_path = None
        else:
            if config_argument is not None:
                config_path = config_argument.expanduser().resolve()
            elif state.get("schema_version") == STATE_SCHEMA_VERSION:
                config_path = Path(state["config_path"]).expanduser().resolve()
            else:
                config_path = paths.codex_home / "config.toml"

            if state.get("schema_version") == STATE_SCHEMA_VERSION:
                recorded = Path(state["config_path"]).expanduser().resolve()
                if recorded != config_path:
                    raise StateError(
                        f"state.json belongs to {recorded}, not requested config {config_path}"
                    )
            validate_config_path(config_path, paths)

            if not config_path.exists():
                raise StateError(f"Codex config not found: {config_path}")

            config_before = snapshot(config_path)
            text = config_before.content.decode("utf-8")
            current = read_notify(text)
            installed = state["installed_notify"]
            original = state["original_notify"]

            if command_contains_tool(
                original, installed, paths.target_notify
            ) or command_chain_looks_like_tool(original):
                raise StateError("state.json contains a recursive original notify command")

            changed = False
            if command_is_tool(current, installed, paths.target_notify):
                original_present = state.get("original_notify_present", bool(original))
                updated = (
                    set_notify(text, original)
                    if original_present
                    else remove_notify(text)
                )
                changed = True
            elif current:
                rewritten, nested_changed = rewrite_nested_tool(
                    current, installed, paths.target_notify, original
                )
                if nested_changed:
                    updated = set_notify(text, rewritten)
                    changed = True
                else:
                    updated = text
            else:
                updated = text

            backup_path = None
            if changed:
                backup_path = unique_backup(config_path)
                shutil.copy2(config_path, backup_path)
                if snapshot(config_path) != config_before:
                    backup_path.unlink(missing_ok=True)
                    raise StateError("config.toml changed while uninstall was being prepared")
            quarantined: list[tuple[Path, Path]] = []
            try:
                if changed:
                    atomic_write_text(config_path, updated, config_before.mode)
                quarantined = quarantine_managed_files(paths)
            except Exception as error:
                rollback_errors = restore_quarantine(quarantined)
                try:
                    if changed and snapshot(config_path) != config_before:
                        atomic_write_text(
                            config_path,
                            config_before.content.decode("utf-8"),
                            config_before.mode,
                        )
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
                if backup_path:
                    try:
                        backup_path.unlink(missing_ok=True)
                    except Exception as rollback_error:
                        rollback_errors.append(rollback_error)
                if rollback_errors:
                    raise ExceptionGroup(
                        "uninstall failed and rollback was incomplete",
                        [error, *rollback_errors],
                    )
                raise
            cleanup_warnings = delete_quarantine(quarantined)

    if paths.state_dir.exists():
        try:
            paths.state_dir.rmdir()
        except OSError:
            pass

    if not_installed:
        print("codex-turn-sound is not installed")
        return

    print(f"restored_notify={render_array(original) if original else '<removed>'}")
    if backup_path:
        print(f"backup={backup_path}")
    for warning in cleanup_warnings:
        print(f"warning={warning}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    args = parser.parse_args()
    try:
        uninstall(Path(args.config) if args.config else None)
    except (ConfigError, StateError, OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"codex-turn-sound: {error}\n")


if __name__ == "__main__":
    main()
