from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import PACKAGE_VERSION


EXPECTED = {
    "LICENSE",
    "README.md",
    "assets/soft-chime.wav",
    "bin/codex-turn-sound",
    "install.sh",
    "package.json",
    "scripts/common.py",
    "scripts/config_toml.py",
    "scripts/doctor.py",
    "scripts/install.py",
    "scripts/make_sound.py",
    "scripts/notify_runtime.py",
    "scripts/uninstall.py",
    "uninstall.sh",
}


def main() -> int:
    result = subprocess.run(
        ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    package = json.loads(result.stdout)[0]
    manifest = json.loads(Path("package.json").read_text())
    if manifest["version"] != PACKAGE_VERSION:
        print("package.json and runtime version constants differ", file=sys.stderr)
        return 1
    files = {entry["path"]: entry for entry in package["files"]}
    actual = set(files)
    if actual != EXPECTED:
        print(f"unexpected package files: {sorted(actual - EXPECTED)}", file=sys.stderr)
        print(f"missing package files: {sorted(EXPECTED - actual)}", file=sys.stderr)
        return 1
    if files["bin/codex-turn-sound"]["mode"] != 0o755:
        print("bin/codex-turn-sound is not executable in the package", file=sys.stderr)
        return 1
    if package.get("bundled"):
        print("unexpected bundled dependencies", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "id": package["id"],
                "integrity": package["integrity"],
                "files": sorted(actual),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
