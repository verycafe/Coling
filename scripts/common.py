#!/usr/bin/env python3
"""Shared state, locking, and atomic filesystem helpers."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


STATE_SCHEMA_VERSION = 2
PACKAGE_VERSION = "0.2.0"


class StateError(RuntimeError):
    """Raised when persisted installation state is unsafe to use."""


@dataclass(frozen=True)
class Paths:
    source_root: Path
    codex_home: Path
    state_dir: Path
    install_root: Path
    target_notify: Path
    state_json: Path
    state_zsh: Path
    lock_file: Path


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    content: bytes = b""
    mode: int = 0o600


def absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def build_paths(source_root: Path) -> Paths:
    codex_home_value = os.environ.get("CODEX_HOME")
    codex_home = absolute_path(codex_home_value or Path.home() / ".codex")
    state_value = os.environ.get("CODEX_TURN_SOUND_STATE_DIR")
    state_dir = absolute_path(state_value or codex_home / "codex-turn-sound")
    install_root = state_dir / "app"
    return Paths(
        source_root=absolute_path(source_root),
        codex_home=codex_home,
        state_dir=state_dir,
        install_root=install_root,
        target_notify=install_root / "bin" / "codex-turn-sound",
        state_json=state_dir / "state.json",
        state_zsh=state_dir / "original-notify.zsh",
        lock_file=codex_home / ".codex-turn-sound.lock",
    )


def validate_runtime_paths(paths: Paths) -> None:
    source = paths.source_root
    target = paths.install_root
    if paths.state_dir in {Path("/"), Path.home().resolve(), paths.codex_home}:
        raise StateError(f"Unsafe state directory: {paths.state_dir}")
    if source == target:
        return
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise StateError(
            f"Source and install paths overlap unsafely: source={source}, target={target}"
        )


def validate_config_path(config_path: Path, paths: Paths) -> None:
    validate_runtime_paths(paths)
    if config_path == paths.state_dir or config_path.is_relative_to(paths.state_dir):
        raise StateError("config.toml cannot be stored inside codex-turn-sound state")
    if config_path == paths.source_root or config_path.is_relative_to(paths.source_root):
        raise StateError("config.toml cannot be stored inside the runtime source")
    if config_path == paths.lock_file:
        raise StateError("config.toml cannot replace the installation lock")


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise StateError(f"Expected a directory: {path}")
    path.chmod(0o700)


def ensure_managed_file(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise StateError(f"Managed state path is not a regular file: {path}")
    if details.st_uid != os.getuid() or details.st_nlink != 1:
        raise StateError(f"Managed state path has unsafe ownership or links: {path}")


@contextmanager
def installation_lock(paths: Paths) -> Iterator[None]:
    paths.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(paths.lock_file, flags, 0o600)
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.getuid()
        or details.st_nlink != 1
    ):
        os.close(descriptor)
        raise StateError(f"Unsafe lock file: {paths.lock_file}")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def snapshot(path: Path) -> FileSnapshot:
    ensure_managed_file(path)
    if not path.exists():
        return FileSnapshot(False)
    return FileSnapshot(True, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        path.chmod(mode)
        _fsync_directory(path.parent)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, content.encode("utf-8"), mode)


def restore_snapshot(path: Path, saved: FileSnapshot) -> None:
    if saved.exists:
        atomic_write_bytes(path, saved.content, saved.mode)
    else:
        path.unlink(missing_ok=True)


def _string_array(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StateError(f"Invalid {field} in state.json")
    if any("\0" in item for item in value):
        raise StateError(f"NUL byte in {field} in state.json")
    return list(value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StateError(f"Duplicate key in state.json: {key}")
        result[key] = value
    return result


def load_state(path: Path) -> dict | None:
    ensure_managed_file(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise StateError(f"state.json is damaged: {error}") from error
    if not isinstance(payload, dict):
        raise StateError("state.json must contain an object")

    schema = payload.get("schema_version")
    if schema not in {None, 1, STATE_SCHEMA_VERSION}:
        raise StateError(f"Unsupported state schema: {schema}")
    payload["installed_notify"] = _string_array(
        payload.get("installed_notify"), "installed_notify"
    )
    payload["original_notify"] = _string_array(
        payload.get("original_notify", []), "original_notify"
    )
    sound_path = payload.get("sound_path")
    if not isinstance(sound_path, str):
        raise StateError("Invalid sound_path in state.json")
    if schema == STATE_SCHEMA_VERSION:
        installed_notify = payload["installed_notify"]
        if (
            len(installed_notify) != 2
            or installed_notify[1] != "turn-ended"
        ):
            raise StateError("Invalid installed_notify command in state.json")
        config_path = payload.get("config_path")
        if not isinstance(config_path, str) or not Path(config_path).is_absolute():
            raise StateError("Invalid config_path in state.json")
        present = payload.get("original_notify_present")
        if not isinstance(present, bool):
            raise StateError("Invalid original_notify_present in state.json")
        sound_kind = payload.get("sound_kind")
        if sound_kind not in {"bundled", "custom"}:
            raise StateError("Invalid sound_kind in state.json")
        sound_sha256 = payload.get("sound_sha256")
        if (
            not isinstance(sound_sha256, str)
            or len(sound_sha256) != 64
            or any(char not in "0123456789abcdef" for char in sound_sha256)
        ):
            raise StateError("Invalid sound_sha256 in state.json")
        if not Path(sound_path).is_absolute():
            raise StateError("sound_path must be absolute")
        if not Path(installed_notify[0]).is_absolute():
            raise StateError("installed notify path must be absolute")
    return payload


def validate_state_for_paths(payload: dict | None, paths: Paths) -> None:
    if payload is None:
        return
    expected = [str(paths.target_notify), "turn-ended"]
    installed = payload["installed_notify"]
    if installed != expected:
        raise StateError("state.json installed_notify does not match this installation")
    original = payload["original_notify"]
    if command_contains_tool(original, installed, paths.target_notify):
        raise StateError("state.json original_notify recursively contains this installation")
    if command_chain_looks_like_tool(original):
        raise StateError("state.json original_notify contains an unrecognized sound runtime")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        return
    sound = Path(payload["sound_path"]).resolve()
    assets = (paths.install_root / "assets").resolve()
    if not sound.is_relative_to(assets):
        raise StateError("state.json sound_path is outside the managed assets directory")


def command_is_tool(command: list[str] | None, installed: list[str], target: Path) -> bool:
    if not command:
        return False
    if command == installed:
        return True
    try:
        executable = absolute_path(command[0])
    except OSError:
        return False
    return executable == target.resolve() and command[1:2] == ["turn-ended"]


def command_looks_like_tool(command: list[str] | None) -> bool:
    if not command or command[1:2] != ["turn-ended"]:
        return False
    return Path(command[0]).name == "codex-turn-sound"


def rewrite_nested_tool(
    command: list[str],
    installed: list[str],
    target: Path,
    replacement: list[str],
    depth: int = 0,
) -> tuple[list[str], bool]:
    if depth > 8:
        raise StateError("notify wrapper nesting is too deep")

    updated = list(command)
    changed_any = False
    index = 0
    while index + 1 < len(updated):
        if updated[index] != "--previous-notify":
            index += 1
            continue
        try:
            nested_value = json.loads(updated[index + 1])
        except json.JSONDecodeError:
            index += 2
            continue
        if not isinstance(nested_value, list) or not all(
            isinstance(item, str) for item in nested_value
        ):
            index += 2
            continue
        nested = list(nested_value)
        if command_is_tool(nested, installed, target):
            if replacement:
                updated[index + 1] = json.dumps(
                    replacement, ensure_ascii=False, separators=(",", ":")
                )
            else:
                del updated[index : index + 2]
                changed_any = True
                continue
            changed_any = True
            index += 2
            continue
        rewritten, changed = rewrite_nested_tool(
            nested, installed, target, replacement, depth + 1
        )
        if changed:
            updated[index + 1] = json.dumps(
                rewritten, ensure_ascii=False, separators=(",", ":")
            )
            changed_any = True
        index += 2
    return updated, changed_any


def command_contains_tool(
    command: list[str] | None, installed: list[str], target: Path
) -> bool:
    if command_is_tool(command, installed, target):
        return True
    if not command:
        return False
    _, changed = rewrite_nested_tool(command, installed, target, [])
    return changed


def command_chain_looks_like_tool(command: list[str] | None, depth: int = 0) -> bool:
    if command_looks_like_tool(command):
        return True
    if not command or depth > 8:
        return False
    for index in range(len(command) - 1):
        if command[index] != "--previous-notify":
            continue
        try:
            nested = json.loads(command[index + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(nested, list) and all(isinstance(item, str) for item in nested):
            if command_chain_looks_like_tool(list(nested), depth + 1):
                return True
    return False
