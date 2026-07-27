from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from config_toml import ConfigError, read_notify, remove_notify, set_notify


class ConfigTomlTests(unittest.TestCase):
    def test_multiline_string_content_is_not_treated_as_toml_structure(self) -> None:
        original = (
            'instructions = """\n'
            'notify = ["inside-string"]\n'
            "[not-a-table]\n"
            '"""\n'
        )
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["notify"], ["/tmp/tool", "turn-ended"])
        self.assertEqual(
            parsed["instructions"],
            'notify = ["inside-string"]\n[not-a-table]\n',
        )

    def test_quoted_notify_key_is_replaced_without_duplicate(self) -> None:
        original = '"notify" = ["/bin/echo", "old"]\n'
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        self.assertEqual(read_notify(updated), ["/tmp/tool", "turn-ended"])
        self.assertEqual(list(tomllib.loads(updated)), ["notify"])

    def test_multiline_array_round_trip(self) -> None:
        original = (
            "notify = [\n"
            "  '/bin/echo',\n"
            "  'old',\n"
            "]\n"
            "\n"
            "[model]\n"
            'name = "test"\n'
        )
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        restored = set_notify(updated, ["/bin/echo", "old"])
        self.assertEqual(read_notify(restored), ["/bin/echo", "old"])
        self.assertEqual(tomllib.loads(restored)["model"]["name"], "test")

    def test_remove_notify_preserves_other_values(self) -> None:
        original = 'alpha = 1\nnotify = ["/bin/echo"]\n\n[model]\nname = "x"\n'
        updated = remove_notify(original)
        parsed = tomllib.loads(updated)
        self.assertNotIn("notify", parsed)
        self.assertEqual(parsed["alpha"], 1)
        self.assertEqual(parsed["model"]["name"], "x")

    def test_invalid_notify_type_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            read_notify('notify = "not-an-array"\n')

    def test_unicode_is_valid_toml(self) -> None:
        updated = set_notify("", ["/tmp/通知", "😀"])
        self.assertEqual(tomllib.loads(updated)["notify"], ["/tmp/通知", "😀"])

    def test_four_quote_multiline_string_ending(self) -> None:
        original = 'note = """abc""""\nnotify = ["/bin/echo"]\n'
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["note"], 'abc"')
        self.assertEqual(parsed["notify"], ["/tmp/tool", "turn-ended"])

    def test_five_quote_multiline_string_ending(self) -> None:
        original = 'note = """abc"""""\nnotify = ["/bin/echo"]\n'
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["note"], 'abc""')
        self.assertEqual(parsed["notify"], ["/tmp/tool", "turn-ended"])

    def test_crlf_is_preserved_for_replacement(self) -> None:
        original = 'notify = ["/bin/echo"]\r\n\r\n[model]\r\nname = "x"\r\n'
        updated = set_notify(original, ["/tmp/tool", "turn-ended"])
        self.assertIn("\r\n", updated)
        self.assertNotIn("\n", updated.replace("\r\n", ""))
        self.assertEqual(read_notify(updated), ["/tmp/tool", "turn-ended"])

    def test_del_control_character_is_rendered_as_toml_escape(self) -> None:
        updated = set_notify("", ["/bin/echo", "\x7f"])
        self.assertIn("\\u007F", updated)
        self.assertEqual(read_notify(updated), ["/bin/echo", "\x7f"])


if __name__ == "__main__":
    unittest.main()
