"""Source safety checks for Telegram webview return after Flow consent."""
from pathlib import Path
import unittest

JS = Path(__file__).resolve().parents[1] / "api/web/flow-account.js"


class TelegramConsentReturnTests(unittest.TestCase):
    def test_close_only_after_server_confirmed_approval(self):
        code = JS.read_text()
        self.assertIn("if (approved) closeApprovedTelegramMiniApp()", code)
        self.assertIn("if (dialog?.dataset.state !== 'approved') return", code)
        self.assertIn("typeof app.close !== 'function'", code)
        self.assertIn("typeof app?.initData !== 'string' || !app.initData", code)
        self.assertNotIn("window.close()", code)
        self.assertNotIn("window.location = 'tg://", code)


    def test_cabinet_asset_and_server_version_in_sync(self):
        import re
        root = Path(__file__).resolve().parents[1]
        html = (root / "api/web/index.html").read_text(encoding="utf-8")
        main = (root / "api/app/main.py").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        notes = (root / "api/web/release-notes.js").read_text(encoding="utf-8")
        versions = re.findall(r"(?m)^Версия проекта: v(\\d+\\.\\d+)\\.$", readme)
        self.assertEqual(len(versions), 1)
        version = versions[0]
        self.assertIn("/assets/flow-account.js?v=" + version, html)
        self.assertIn('version="' + version + '"', main)
        self.assertIn('"version": "' + version + '"', notes)
        self.assertTrue((root / ("docs/RELEASE_v" + version + ".md")).is_file())


if __name__ == "__main__":
    unittest.main()
