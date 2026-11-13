"""Каталог Telegram: искусственные ID, без токенов и сетевых вызовов в CI."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from bot.pushok_emoji_catalog import build_catalog, catalog_ids
from bot.button_icons import ALLOWED

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fetch_catalog", ROOT / "scripts/fetch_pushok_emoji_catalog.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
TOKEN = "123456:" + "fixture_" * 4


def sticker_set():
    return {"name": "PushokIcons", "sticker_type": "custom_emoji", "stickers": [
        {"type": "custom_emoji", "custom_emoji_id": str(100000000000 + i),
         "emoji": "🐱", "file_id": "fixture_file_" + str(i)} for i in range(12)]}


class CatalogTests(unittest.TestCase):
    def test_all_ids_survive_reordering_without_action_assignment(self):
        raw = sticker_set()
        result = build_catalog(raw)
        raw["stickers"].reverse()
        self.assertEqual(catalog_ids(result), catalog_ids(build_catalog(raw)))
        self.assertNotIn("actions", result)
        self.assertEqual(result["sticker_set"]["stickers"][0]["file_id"], "fixture_file_0")

    def test_wrong_pack_type_count_duplicates_and_malformed_entries(self):
        bad_sets = [[], {}, {**sticker_set(), "name": "Other"},
                    {**sticker_set(), "sticker_type": "regular"},
                    {**sticker_set(), "stickers": sticker_set()["stickers"][:-1]}]
        for key, value in (("type", "regular"), ("custom_emoji_id", 100000),
                           ("custom_emoji_id", "00000"), ("custom_emoji_id", "100000000001"),
                           ("file_id", ""), ("file_id", "bad\nfile"), ("emoji", [])):
            bad = sticker_set()
            bad["stickers"][0][key] = value
            bad_sets.append(bad)
        for bad in bad_sets:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_catalog(bad)

    def test_emoji_is_optional_and_count_changes_require_explicit_expectation(self):
        raw = sticker_set()
        raw["stickers"][0].pop("emoji")
        raw["stickers"].pop()
        self.assertIsNone(build_catalog(raw, 11)["sticker_set"]["stickers"][0]["emoji"])
        for count in (0, 201, True, None):
            with self.assertRaises(ValueError):
                build_catalog(raw, count)

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_fetch_uses_fixed_read_only_method_and_sanitizes_response(self):
        raw = sticker_set()
        raw["unknown"] = "not copied"
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"ok": True, "result": raw}).encode()
        with patch.object(module, "build_opener") as build:
            build.return_value.open.return_value = response
            catalog = module.fetch_catalog(TOKEN)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.telegram.org/bot" + TOKEN + "/getStickerSet")
            self.assertEqual(json.loads(request.data), {"name": "PushokIcons"})
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 20)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], module.NoRedirect)
        self.assertEqual(len(catalog_ids(catalog)), 12)
        self.assertNotIn("not copied", json.dumps(catalog))
        self.assertIsNone(module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.org"))

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_errors_never_expose_token_or_raw_response(self):
        url = "https://api.telegram.org/bot" + TOKEN + "/getStickerSet"
        for error in (HTTPError(url, 401, TOKEN, {}, io.BytesIO(TOKEN.encode())),
                      URLError(url), OSError(TOKEN)):
            with patch.object(module, "build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(ValueError) as caught:
                    module.fetch_catalog(TOKEN)
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertEqual(build.return_value.open.call_count, 1)
        for raw in (TOKEN.encode(), b"[]", b"null", b"x" * (module.MAX_RESPONSE + 1),
                    json.dumps({"ok": False, "description": TOKEN}).encode()):
            with patch.object(module, "build_opener") as build:
                build.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(ValueError) as caught:
                    module.fetch_catalog(TOKEN)
                self.assertNotIn(TOKEN, str(caught.exception))

    def test_invalid_environment_and_token_do_not_make_request(self):
        with patch.object(module, "build_opener") as build:
            with self.assertRaises(ValueError):
                module.fetch_catalog("https://bad/token")
            with patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "1"}), self.assertRaises(ValueError):
                module.fetch_catalog(TOKEN)
            build.assert_not_called()

    def test_atomic_catalog_save_keeps_previous_output_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            module.save_catalog(path, build_catalog(sticker_set()))
            original = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch.object(module.os, "replace", side_effect=OSError), self.assertRaises(OSError):
                module.save_catalog(path, {"new": True})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["catalog.json"])

    def test_prepare_cli_checks_catalog_membership_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, ids, catalog, output = [root / name for name in ("manifest.json", "ids.json", "catalog.json", "icons.env")]
            manifest.write_text(json.dumps({"schema": "pushok-telegram-emoji-manifest-v1", "actions": dict.fromkeys(ALLOWED, {})}))
            ids.write_text('{"advertise":"100000000000"}')
            data = build_catalog(sticker_set())
            catalog.write_text(json.dumps(data))
            command = [sys.executable, str(ROOT / "scripts/prepare_pushok_emoji_ids.py"),
                       "--manifest", str(manifest), "--ids", str(ids), "--catalog", str(catalog),
                       "--format", "env", "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = output.read_text()
            self.assertNotIn("ICONS_ENABLED", original)
            ids.write_text('{"advertise":"999999999999"}')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), original)
            ids.write_text('{"advertise":"100000000000"}')
            invalid = copy.deepcopy(data)
            invalid["environment"] = "test"
            for bad in (None, [], {}, invalid):
                catalog.write_text(json.dumps(bad))
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(output.read_text(), original)


if __name__ == "__main__":
    unittest.main()
