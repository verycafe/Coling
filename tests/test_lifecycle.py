from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import common
import doctor
import install
import uninstall
from config_toml import read_notify, render_array


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex-home"
        self.state_dir = self.codex_home / "codex-turn-sound"
        self.config = self.codex_home / "config.toml"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.codex_home),
                "CODEX_TURN_SOUND_STATE_DIR": str(self.state_dir),
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def write_config(self, command: list[str]) -> str:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        text = f"notify = {render_array(command)}\n\n[model]\nname = \"x\"\n"
        self.config.write_text(text)
        return text

    def quiet_install(self, sound: Path | None = None) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            install.install(self.config, sound)

    def quiet_uninstall(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            uninstall.uninstall(self.config)

    def test_install_uninstall_round_trip_and_cleanup(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.state_dir / "state.json").stat().st_mode), 0o600
        )
        self.assertEqual(
            stat.S_IMODE(
                (self.codex_home / ".codex-turn-sound.lock").stat().st_mode
            ),
            0o600,
        )
        self.assertFalse((self.state_dir / "original-notify.zsh").exists())

        self.quiet_uninstall()
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse(self.state_dir.exists())
        self.assertTrue((self.codex_home / ".codex-turn-sound.lock").is_file())

    def test_empty_notify_is_restored_instead_of_removed(self) -> None:
        original = self.write_config([])
        self.quiet_install()
        self.quiet_uninstall()
        self.assertEqual(self.config.read_text(), original)
        self.assertEqual(read_notify(self.config.read_text()), [])

    def test_uninstall_when_absent_leaves_no_state_directory(self) -> None:
        self.config.parent.mkdir(parents=True)
        self.config.write_text('[model]\nname = "x"\n')
        self.quiet_uninstall()
        self.assertFalse(self.state_dir.exists())

    def test_missing_state_reinstall_fails_closed(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        before = self.config.read_bytes()
        (self.state_dir / "state.json").unlink()
        with self.assertRaises(common.StateError):
            self.quiet_install()
        self.assertEqual(self.config.read_bytes(), before)

    def test_corrupt_state_uninstall_fails_closed(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        before = self.config.read_bytes()
        (self.state_dir / "state.json").write_text("{")
        with self.assertRaises(common.StateError):
            self.quiet_uninstall()
        self.assertEqual(self.config.read_bytes(), before)

    def test_duplicate_state_keys_are_rejected(self) -> None:
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "state.json").write_text(
            '{"installed_notify":[],"installed_notify":[],"original_notify":[],'
            '"sound_path":"/tmp/sound"}'
        )
        with self.assertRaises(common.StateError):
            common.load_state(self.state_dir / "state.json")

    def test_empty_installed_notify_is_rejected_by_all_entrypoints(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        state_file = self.state_dir / "state.json"
        state = json.loads(state_file.read_text())
        state["installed_notify"] = []
        state_file.write_text(json.dumps(state))

        with self.assertRaises(common.StateError):
            common.load_state(state_file)
        with self.assertRaises(common.StateError):
            self.quiet_uninstall()

        diagnosis = doctor.diagnose()
        self.assertFalse(diagnosis["ok"])
        self.assertTrue(
            any("installed_notify command" in issue for issue in diagnosis["issues"])
        )

        env = os.environ.copy()
        env["CODEX_TURN_SOUND"] = "off"
        result = subprocess.run(
            [
                str(REPO / "bin/codex-turn-sound"),
                "turn-ended",
                '{"type":"agent-turn-complete"}',
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_state_io_errors_are_reported_as_state_errors(self) -> None:
        self.state_dir.mkdir(parents=True)
        state_file = self.state_dir / "state.json"
        state_file.write_text("{}")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(common.StateError):
                common.load_state(state_file)

    def test_second_active_config_is_rejected(self) -> None:
        self.write_config(["/bin/echo", "A"])
        self.quiet_install()
        second = self.codex_home / "other.toml"
        second.write_text('notify = ["/bin/echo", "B"]\n')
        with self.assertRaises(common.StateError):
            with contextlib.redirect_stdout(io.StringIO()):
                install.install(second)
        self.assertEqual(read_notify(second.read_text()), ["/bin/echo", "B"])

    def test_copied_active_config_cannot_rebind_state(self) -> None:
        original = self.write_config(["/bin/echo", "A"])
        self.quiet_install()
        second = self.codex_home / "copied.toml"
        second.write_bytes(self.config.read_bytes())

        with self.assertRaises(common.StateError):
            with contextlib.redirect_stdout(io.StringIO()):
                install.install(second)

        self.quiet_uninstall()
        self.assertEqual(self.config.read_text(), original)

    def test_multiline_string_install_creates_real_root_notify(self) -> None:
        self.config.parent.mkdir(parents=True)
        original_instruction = 'notify = ["inside-string"]\n[not-a-table]\n'
        self.config.write_text(f'instructions = """\n{original_instruction}"""\n')
        self.quiet_install()
        import tomllib

        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["instructions"], original_instruction)
        self.assertEqual(parsed["notify"][1], "turn-ended")

    def test_computer_use_legacy_chain_upgrades_and_restores(self) -> None:
        paths = common.build_paths(REPO)
        installed = [str(paths.target_notify), "turn-ended"]
        old_original = ["/bin/echo", "old"]
        outer = [
            "/tmp/computer-use",
            "turn-ended",
            "--previous-notify",
            json.dumps(installed),
        ]
        self.write_config(outer)
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "state.json").write_text(
            json.dumps(
                {
                    "installed_notify": installed,
                    "original_notify": old_original,
                    "sound_path": str(paths.install_root / "assets/soft-chime.wav"),
                }
            )
        )
        self.quiet_install()
        state = json.loads((self.state_dir / "state.json").read_text())
        preserved_outer = state["original_notify"]
        previous_index = preserved_outer.index("--previous-notify") + 1
        self.assertEqual(json.loads(preserved_outer[previous_index]), old_original)

        self.quiet_uninstall()
        restored = read_notify(self.config.read_text())
        self.assertEqual(restored, preserved_outer)

    def test_state_write_failure_rolls_back_everything(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.state_dir.mkdir(parents=True)
        old_app = self.state_dir / "app"
        old_app.mkdir()
        (old_app / "marker").write_text("old")
        real_write = install.atomic_write_text

        def fail_state_json(path: Path, content: str, mode: int = 0o600) -> None:
            if path.name == "state.json":
                raise OSError("injected state failure")
            real_write(path, content, mode)

        with mock.patch.object(install, "atomic_write_text", side_effect=fail_state_json):
            with self.assertRaises(OSError):
                self.quiet_install()
        self.assertEqual(self.config.read_text(), original)
        self.assertEqual((old_app / "marker").read_text(), "old")
        self.assertFalse((self.state_dir / "state.json").exists())

    def test_staging_failure_removes_partial_stage(self) -> None:
        original = self.write_config(["/bin/echo", "old"])

        def fail_copytree(_source, destination, **_kwargs):
            Path(destination).mkdir(parents=True)
            (Path(destination) / "partial").write_text("partial")
            raise OSError("injected copy failure")

        with mock.patch.object(install.shutil, "copytree", side_effect=fail_copytree):
            with self.assertRaises(OSError):
                self.quiet_install()
        self.assertEqual(self.config.read_text(), original)
        self.assertEqual(list(self.state_dir.glob(".app-stage-*")), [])

    def test_uninstall_config_failure_restores_active_installation(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        installed_config = self.config.read_bytes()
        runtime = self.state_dir / "app" / "bin" / "codex-turn-sound"
        state_file = self.state_dir / "state.json"

        with mock.patch.object(
            uninstall,
            "atomic_write_text",
            side_effect=OSError("injected uninstall config failure"),
        ):
            with self.assertRaises(OSError):
                self.quiet_uninstall()
        self.assertNotEqual(self.config.read_text(), original)
        self.assertEqual(self.config.read_bytes(), installed_config)
        self.assertTrue(runtime.is_file())
        self.assertTrue(state_file.is_file())

    def test_uninstall_cleanup_failure_leaves_only_quarantine(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        with mock.patch.object(
            uninstall.shutil,
            "rmtree",
            side_effect=OSError("injected cleanup failure"),
        ):
            self.quiet_uninstall()
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse((self.state_dir / "app").exists())
        self.assertFalse((self.state_dir / "state.json").exists())
        quarantines = list(self.state_dir.glob(".uninstall-*-app"))
        self.assertEqual(len(quarantines), 1)

    def test_uninstall_recovers_after_config_commit_before_cleanup(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.quiet_install()

        # Simulate a process exit after restoring config but before quarantining files.
        self.config.write_text(original)
        self.quiet_uninstall()

        self.assertEqual(self.config.read_text(), original)
        self.assertFalse(self.state_dir.exists())

    def test_del_character_notify_round_trip(self) -> None:
        original = 'notify = ["/bin/echo", "\\u007F"]\n'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(original)

        self.quiet_install()
        self.quiet_uninstall()

        self.assertEqual(self.config.read_text(), original)
        self.assertEqual(read_notify(self.config.read_text()), ["/bin/echo", "\x7f"])

    def test_config_commit_failure_restores_runtime_and_state(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        self.state_dir.mkdir(parents=True)
        old_app = self.state_dir / "app"
        old_app.mkdir()
        (old_app / "marker").write_text("old")
        real_write = install.atomic_write_text

        def fail_config(path: Path, content: str, mode: int = 0o600) -> None:
            if path == self.config.resolve():
                raise OSError("injected config failure")
            real_write(path, content, mode)

        with mock.patch.object(install, "atomic_write_text", side_effect=fail_config):
            with self.assertRaises(OSError):
                self.quiet_install()
        self.assertEqual(self.config.read_text(), original)
        self.assertEqual((old_app / "marker").read_text(), "old")
        self.assertFalse((self.state_dir / "state.json").exists())
        self.assertFalse((self.state_dir / "original-notify.zsh").exists())

    def test_backups_are_unique(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        self.quiet_install()
        backups = list(self.codex_home.glob("config.toml.bak-install-*"))
        self.assertEqual(len(backups), 2)
        self.assertEqual(len({path.name for path in backups}), 2)

    def test_custom_sound_is_validated_and_copied(self) -> None:
        self.write_config(["/bin/echo", "old"])
        custom = self.root / "custom.wav"
        custom.write_bytes((REPO / "assets" / "soft-chime.wav").read_bytes())
        self.quiet_install(custom)
        custom.unlink()
        state = json.loads((self.state_dir / "state.json").read_text())
        installed_sound = Path(state["sound_path"])
        self.assertTrue(installed_sound.is_file())
        self.assertEqual(installed_sound.name, "custom-sound.wav")

    def test_custom_sound_survives_upgrade_without_sound_flag(self) -> None:
        self.write_config(["/bin/echo", "old"])
        custom = self.root / "custom.wav"
        custom.write_bytes((REPO / "assets" / "soft-chime.wav").read_bytes())
        self.quiet_install(custom)
        first = json.loads((self.state_dir / "state.json").read_text())
        self.quiet_install()
        second = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(second["sound_kind"], "custom")
        self.assertEqual(second["sound_sha256"], first["sound_sha256"])
        self.assertEqual(Path(second["sound_path"]).name, "custom-sound.wav")

    def test_changed_custom_sound_requires_explicit_acceptance(self) -> None:
        self.write_config(["/bin/echo", "old"])
        custom = self.root / "custom.wav"
        custom.write_bytes((REPO / "assets" / "soft-chime.wav").read_bytes())
        self.quiet_install(custom)
        state = json.loads((self.state_dir / "state.json").read_text())
        managed = Path(state["sound_path"])
        changed = bytearray(managed.read_bytes())
        changed[-20] ^= 1
        managed.write_bytes(changed)
        with self.assertRaises(common.StateError):
            self.quiet_install()
        with contextlib.redirect_stdout(io.StringIO()):
            install.install(self.config, managed)
        updated = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(updated["sound_sha256"], install.file_sha256(Path(updated["sound_path"])))

    def test_reset_sound_returns_to_bundled_sound(self) -> None:
        self.write_config(["/bin/echo", "old"])
        custom = self.root / "custom.wav"
        custom.write_bytes((REPO / "assets" / "soft-chime.wav").read_bytes())
        self.quiet_install(custom)
        with contextlib.redirect_stdout(io.StringIO()):
            install.install(self.config, reset_sound=True)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["sound_kind"], "bundled")
        self.assertEqual(Path(state["sound_path"]).name, "soft-chime.wav")

    def test_directory_is_not_accepted_as_sound(self) -> None:
        original = self.write_config(["/bin/echo", "old"])
        with self.assertRaises(FileNotFoundError):
            self.quiet_install(self.root)
        self.assertEqual(self.config.read_text(), original)

    def test_relative_state_directory_is_resolved(self) -> None:
        with mock.patch.dict(
            os.environ, {"CODEX_TURN_SOUND_STATE_DIR": "relative-state"}, clear=False
        ):
            paths = common.build_paths(REPO)
        self.assertTrue(paths.state_dir.is_absolute())

    def test_source_state_overlap_is_rejected(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CODEX_TURN_SOUND_STATE_DIR": str(REPO / ".state")},
            clear=False,
        ):
            paths = common.build_paths(REPO)
        with self.assertRaises(common.StateError):
            common.validate_runtime_paths(paths)

    def test_state_directory_cannot_be_codex_home(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CODEX_TURN_SOUND_STATE_DIR": str(self.codex_home)},
            clear=False,
        ):
            paths = common.build_paths(REPO)
        with self.assertRaises(common.StateError):
            common.validate_runtime_paths(paths)

    def test_uninstall_rejects_state_directory_equal_to_codex_home(self) -> None:
        self.config.parent.mkdir(parents=True)
        self.config.write_text('[model]\nname = "x"\n')
        with mock.patch.dict(
            os.environ,
            {"CODEX_TURN_SOUND_STATE_DIR": str(self.codex_home)},
            clear=False,
        ):
            with self.assertRaises(common.StateError):
                self.quiet_uninstall()
        self.assertTrue(self.config.is_file())

    def test_custom_state_directories_share_one_stable_lock(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CODEX_TURN_SOUND_STATE_DIR": str(self.root / "state-a")},
            clear=False,
        ):
            first = common.build_paths(REPO)
        with mock.patch.dict(
            os.environ,
            {"CODEX_TURN_SOUND_STATE_DIR": str(self.root / "state-b")},
            clear=False,
        ):
            second = common.build_paths(REPO)
        self.assertEqual(first.lock_file, second.lock_file)
        self.assertEqual(
            first.lock_file, self.codex_home.resolve() / ".codex-turn-sound.lock"
        )

    def test_codex_home_controls_default_paths(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_TURN_SOUND_STATE_DIR", None)
            paths = common.build_paths(REPO)
        self.assertEqual(
            paths.state_dir, (self.codex_home / "codex-turn-sound").resolve()
        )
        self.assertEqual(paths.codex_home / "config.toml", self.config.resolve())

    def test_runtime_forwards_payload_without_waiting(self) -> None:
        capture = self.root / "capture.sh"
        output = self.root / "captured.json"
        capture.write_text(
            "#!/bin/sh\n"
            "if IFS= read -r value; then stdin_state=readable; else stdin_state=eof; fi\n"
            "sleep 2\n"
            "python3 -c 'import json,os,sys; "
            "open(os.environ[\"CAPTURE_OUT\"],\"w\").write(json.dumps(sys.argv[1:]))' "
            '"$stdin_state" "$@"\n'
        )
        capture.chmod(0o755)
        self.write_runtime_state([str(capture), "configured"])
        payload = json.dumps(
            {
                "type": "agent-turn-complete",
                "turn-id": "t 1",
                "message": "引号 \" 和换行\n都必须保留",
            },
            ensure_ascii=False,
        )
        env = os.environ.copy()
        env["CODEX_TURN_SOUND"] = "off"
        env["CAPTURE_OUT"] = str(output)
        started = time.monotonic()
        result = subprocess.run(
            [str(REPO / "bin/codex-turn-sound"), "turn-ended", payload],
            env=env,
            check=False,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed, 1.0)
        deadline = time.monotonic() + 4
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(
            json.loads(output.read_text()), ["eof", "configured", payload]
        )

    def test_runtime_skips_recursive_original(self) -> None:
        runtime = REPO / "bin" / "codex-turn-sound"
        paths = common.build_paths(REPO)
        nested = [
            "/tmp/outer",
            "--previous-notify",
            json.dumps([str(paths.target_notify), "turn-ended"]),
        ]
        self.write_runtime_state(nested)
        env = os.environ.copy()
        env["CODEX_TURN_SOUND"] = "off"
        result = subprocess.run(
            [str(runtime), "turn-ended", '{"type":"agent-turn-complete"}'],
            env=env,
            timeout=2,
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_install_rejects_nested_recursive_original(self) -> None:
        paths = common.build_paths(REPO)
        installed = [str(paths.target_notify), "turn-ended"]
        self.write_config(installed)
        self.state_dir.mkdir(parents=True)
        recursive = [
            "/tmp/outer",
            "--previous-notify",
            json.dumps(installed),
        ]
        (self.state_dir / "state.json").write_text(
            json.dumps(
                {
                    "installed_notify": installed,
                    "original_notify": recursive,
                    "sound_path": str(REPO / "assets/soft-chime.wav"),
                }
            )
        )
        with self.assertRaises(common.StateError):
            self.quiet_install()

    def test_uninstall_rewrites_all_nested_occurrences(self) -> None:
        paths = common.build_paths(REPO)
        installed = [str(paths.target_notify), "turn-ended"]
        outer = [
            "/tmp/outer",
            "--previous-notify",
            json.dumps(installed),
            "--previous-notify",
            json.dumps(installed),
        ]
        self.write_config(outer)
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "state.json").write_text(
            json.dumps(
                {
                    "installed_notify": installed,
                    "original_notify": ["/bin/echo", "old"],
                    "sound_path": str(REPO / "assets/soft-chime.wav"),
                }
            )
        )
        self.quiet_uninstall()
        restored = read_notify(self.config.read_text())
        self.assertNotIn(str(paths.target_notify), json.dumps(restored))
        self.assertEqual(restored.count("--previous-notify"), 2)

    def test_doctor_reports_health_and_sound_damage(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        healthy = doctor.diagnose()
        self.assertTrue(healthy["ok"], healthy["issues"])
        Path(healthy["sound_path"]).unlink()
        damaged = doctor.diagnose()
        self.assertFalse(damaged["ok"])
        self.assertTrue(
            any("sound is missing" in issue for issue in damaged["issues"])
        )

    def test_doctor_reports_recursive_state_without_throwing(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        state_file = self.state_dir / "state.json"
        state = json.loads(state_file.read_text())
        state["original_notify"] = state["installed_notify"]
        state_file.write_text(json.dumps(state))
        result = doctor.diagnose()
        self.assertFalse(result["ok"])
        self.assertTrue(any("recursively" in issue for issue in result["issues"]))

    def test_doctor_handles_non_object_package_json(self) -> None:
        self.write_config(["/bin/echo", "old"])
        self.quiet_install()
        (self.state_dir / "app" / "package.json").write_text("[]")
        result = doctor.diagnose()
        self.assertFalse(result["ok"])
        self.assertTrue(any("must contain an object" in issue for issue in result["issues"]))

    def write_runtime_state(self, original: list[str]) -> None:
        paths = common.build_paths(REPO)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "state.json").write_text(
            json.dumps(
                {
                    "installed_notify": [str(paths.target_notify), "turn-ended"],
                    "original_notify": original,
                    "sound_path": str(REPO / "assets/soft-chime.wav"),
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
