#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' "python3 is required to install codex-turn-sound." >&2
  exit 1
fi

exec python3 "$ROOT_DIR/scripts/install.py" "$@"
