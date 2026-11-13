"""Проверки миниатюр, URL, приватности и локальной страницы выбора."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from PIL import Image

from bot.button_icons import ALLOWED
from bot.pushok_emoji_catalog import build_catalog
from bot.pushok_emoji_picker import ACTIONS, normalize_preview, render_picker

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fetch_picker", ROOT / "scripts/fetch_pushok_emoji_catalog.py")
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)
TOKEN = "123456:" + "fixture_" * 4


def picture(fmt="PNG", size=(100, 100)):
    stream = io.BytesIO()
    Image.new("RGB", size, "orange").save(stream, format=fmt)
    return stream.getvalue()


def catalog(animated=False, thumbnail=False):
    entry = {"type": "custom_emoji", "custom_emoji_id": "9007199254740993123",
             "emoji": "🐱", "file_id": "fixture_original", "is_animated": animated, "is_video": False}
    if thumbnail:
        entry["thumbnail"] = {"file_id": "fixture_thumbnail", "unknown": "discard"}
    return build_catalog({"name": "PushokIcons", "sticker_type": "custom_emoji", "stickers": [entry]}, 1)


class PickerTests(unittest.TestCase):
    def test_supported_images_become_small_png(self):
        for fmt in ("WEBP", "JPEG", "PNG"):
            with self.subTest(fmt=fmt):
                normalized = normalize_preview(picture(fmt, (640, 320)))
                with Image.open(io.BytesIO(normalized)) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (160, 80))
                    self.assertEqual(image.mode, "RGBA")
                    self.assertNotIn("exif", image.info)

    def test_damaged_oversized_nonimage_and_animation_rejected(self):
        for raw in (b"<svg onload='alert(1)'/>", b"broken", b"x" * (512 * 1024 + 1),
                    picture(size=(1025, 5)), picture("GIF")):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                normalize_preview(raw)

    def test_source_metadata_is_not_embedded_in_html_image(self):
        image = Image.new("RGB", (100, 100), "orange")
        exif = Image.Exif(); exif[270] = "fixture-private-metadata"
        source = io.BytesIO(); image.save(source, format="JPEG", exif=exif)
        normalized = normalize_preview(source.getvalue())
        self.assertNotIn(b"fixture-private-metadata", normalized)
        with Image.open(io.BytesIO(normalized)) as actual:
            self.assertEqual(dict(actual.getexif()), {})

    def test_html_contains_only_normalized_images_and_escaped_catalog(self):
        data = catalog()
        data["sticker_set"]["stickers"][0]["emoji"] = '</script><img src=x onerror="alert(1)">'
        html = render_picker(data, {"9007199254740993123": picture()})
        self.assertEqual(set(ACTIONS), ALLOWED)
        self.assertNotIn(TOKEN, html)
        self.assertNotIn("fixture_original", html)
        self.assertNotIn("api.telegram.org", html)
        self.assertNotIn('</script><img src=x', html)
        self.assertIn('\\u003c/script>', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("default-src 'none'", html)
        self.assertNotIn("__SCRIPT_HASH__", html)
        self.assertNotIn("__CATALOG_DATA__", html)
        with self.assertRaises(ValueError):
            render_picker(data, {"12345678": picture()})

    def test_missing_previews_stay_explicitly_unavailable(self):
        html = render_picker(catalog(), {})
        self.assertIn('"image": null', html)
        self.assertNotIn("data:image/png;base64,", html)

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_fetch_prefers_thumbnail_and_uses_fixed_download_host(self):
        data = catalog(animated=True, thumbnail=True)
        with patch.object(fetch, "read_method", return_value={"file_path": "thumbnails/file_1.webp"}) as api, \
             patch.object(fetch, "read_telegram", return_value=picture("WEBP")) as download:
            previews = fetch.fetch_previews(TOKEN, data)
        self.assertEqual(api.call_args.args, (TOKEN, "getFile", {"file_id": "fixture_thumbnail"}))
        request, limit = download.call_args.args
        self.assertEqual(request.full_url, "https://api.telegram.org/file/bot" + TOKEN + "/thumbnails/file_1.webp")
        self.assertEqual(limit, 512 * 1024)
        self.assertEqual(set(previews), {"9007199254740993123"})
        with patch.object(fetch, "read_method") as api:
            self.assertEqual(fetch.fetch_previews(TOKEN, catalog(animated=True)), {})
            api.assert_not_called()

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_unsafe_file_paths_and_sizes_never_download(self):
        paths = ["https://evil.example/a.png", "//evil/a.png", "/tmp/a.png", "../a.png",
                 "stickers/../a.png", "stickers/%2e%2e/a.png", "stickers//a.png", "stickers/a.png?x=1",
                 "stickers/a.svg", "stickers/a.tgs", "stickers/a.webm", "stickers\\a.png", None]
        responses = [{"file_path": path} for path in paths] + [
            {"file_path": "stickers/file.png", "file_size": size} for size in (True, -1, 9999999, "12")]
        for response in responses:
            with self.subTest(response=response), patch.object(fetch, "read_method", return_value=response), \
                 patch.object(fetch, "read_telegram") as download:
                self.assertEqual(fetch.fetch_previews(TOKEN, catalog()), {})
                download.assert_not_called()

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_corrupt_preview_error_and_total_limit_do_not_assign_images(self):
        for raw in (b"<html>bad</html>", picture()):
            with patch.object(fetch, "read_method", return_value={"file_path": "stickers/file.png"}), \
                 patch.object(fetch, "read_telegram", return_value=raw), \
                 patch.object(fetch, "MAX_PREVIEWS_TOTAL", 1):
                self.assertEqual(fetch.fetch_previews(TOKEN, catalog()), {})
        with patch.object(fetch, "read_method", side_effect=ValueError(TOKEN)):
            self.assertEqual(fetch.fetch_previews(TOKEN, catalog()), {})

    @patch.dict(os.environ, {"PUSHOK_TELEGRAM_TEST_ENVIRONMENT": "0"})
    def test_redirect_and_http_errors_are_redacted_for_file_requests(self):
        error = HTTPError("https://api.telegram.org/file/bot" + TOKEN, 302, TOKEN, {}, io.BytesIO())
        with patch.object(fetch, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ValueError) as caught:
                fetch.read_telegram(fetch.Request("https://api.telegram.org/file/bot" + TOKEN), 100)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("https:", str(caught.exception))

    def test_metadata_flags_and_thumbnail_are_validated(self):
        for key, value in (("is_animated", "true"), ("is_video", 1), ("thumbnail", []),
                           ("thumbnail", {"file_id": "bad\nvalue"})):
            raw = catalog()["sticker_set"]
            raw["stickers"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_catalog(raw, 1)

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN})
    def test_cli_saves_page_and_catalog_without_enabling_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            output, html = Path(directory) / "catalog.json", Path(directory) / "picker.html"
            with patch.object(fetch.sys, "argv", ["fetch", "--output", str(output), "--html", str(html)]), \
                 patch.object(fetch, "fetch_catalog", return_value=catalog()), \
                 patch.object(fetch, "fetch_previews", return_value={"9007199254740993123": picture()}), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                fetch.main()
            self.assertNotIn(TOKEN, stdout.getvalue() + output.read_text() + html.read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(html.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(p.name for p in Path(directory).iterdir()), {"catalog.json", "picker.html"})


if __name__ == "__main__":
    unittest.main()
