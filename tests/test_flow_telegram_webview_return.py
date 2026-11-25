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


if __name__ == "__main__":
    unittest.main()
