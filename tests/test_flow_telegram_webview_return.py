"""Source safety checks for Telegram webview return after Flow consent."""
import json
from pathlib import Path
import re
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
        root = Path(__file__).resolve().parents[1]
        html = (root / "api/web/index.html").read_text(encoding="utf-8")
        main = (root / "api/app/main.py").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        notes = (root / "api/web/release-notes.js").read_text(encoding="utf-8")
        match = re.search(r'^\s*version="(\d+(?:\.\d+)+)",$', main, re.MULTILINE)
        self.assertIsNotNone(match, "Expected the server application version")
        version = match.group(1)
        self.assertIn(f'"/assets/flow-account.js?v={version}"', html)
        self.assertIn(f"Версия проекта: v{version}.", readme)
        self.assertIn(f"docs/RELEASE_v{version}.md", readme)
        releases, _ = json.JSONDecoder().raw_decode(notes.split("window.pushokReleaseNotes=", 1)[1].lstrip())
        self.assertEqual(releases[0]["version"], version)


if __name__ == "__main__":
    unittest.main()
