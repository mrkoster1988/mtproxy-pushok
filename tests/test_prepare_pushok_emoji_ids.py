"""Проверки ручного соответствия Telegram Custom Emoji ID."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from bot.button_icons import ALLOWED, configured_icons
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("idmap", ROOT / "scripts" / "prepare_pushok_emoji_ids.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class TestIdMapping(unittest.TestCase):
    def setUp(self):
        self.manifest = {"schema": "pushok-telegram-emoji-manifest-v1",
                         "actions": dict.fromkeys(ALLOWED, {})}

    def test_valid_id(self):
        self.assertEqual(module.validate(self.manifest, {"advertise": "1234567890123456789"}),
                         {"advertise": "1234567890123456789"})

    def test_unknown_action(self):
        with self.assertRaises(ValueError):
            module.validate(self.manifest, {"unknown": "1234567890123456789"})

    def test_bad_ids(self):
        for payload in ({"advertise": 123456}, {"advertise": "not-an-id"},
                        {"advertise": "00000"}, {"advertise": "12345\n"},
                        {"advertise": ""}, ["12345"], {}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                module.validate(self.manifest, payload)

    def test_foreign_or_malformed_manifest_rejected(self):
        for manifest in ([], None, {}, {"schema": "wrong", "actions": self.manifest["actions"]},
                         {"schema": self.manifest["schema"], "actions": {"buy": {}}}):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                module.validate(manifest, {"advertise": "123456789"})

    def test_cli_env_round_trip_and_invalid_input_keeps_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, ids, output = (root / name for name in ("manifest.json", "ids.json", "icons.env"))
            manifest.write_text(json.dumps(self.manifest))
            mapping = {"advertise": "1234567890123456789", "miniapp": "9876543210123456789"}
            ids.write_text(json.dumps(mapping))
            command = [sys.executable, str(ROOT / "scripts/prepare_pushok_emoji_ids.py"),
                       "--manifest", str(manifest), "--ids", str(ids),
                       "--format", "env", "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = output.read_text()
            self.assertEqual(len(original.splitlines()), 1)
            self.assertNotIn("PUSHOK_BUTTON_ICONS_ENABLED", original)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            raw = original.strip().split("=", 1)[1][1:-1]
            with patch.dict(os.environ, {"PUSHOK_BUTTON_ICONS_ENABLED": "1", "PUSHOK_BUTTON_EMOJI_JSON": raw}):
                self.assertEqual(configured_icons(), mapping)
            for bad in ('{"advertise":"12345","advertise":"67890"}', '{"buy":"12345"}', '{}', 'oops'):
                ids.write_text(bad)
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(output.read_text(), original)


if __name__ == "__main__":
    unittest.main()
