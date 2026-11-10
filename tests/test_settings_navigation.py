"""v0.050 — admin-only настройки, без обмана о реальной оплате."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.ancestors = {}
        self.ids = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(d["id"])
            self.ancestors[d["id"]] = list(self.stack)
        if tag not in ("input", "img", "meta", "link", "source", "br", "hr"):
            self.stack.append((tag, d.get("id")))

    def handle_endtag(self, tag):
        for idx in range(len(self.stack)-1, -1, -1):
            if self.stack[idx][0] == tag:
                del self.stack[idx:]
                break


class SettingsNavigationTests(unittest.TestCase):
    def test_settings_are_separate_from_operator_and_admin_only(self):
        html = (ROOT / "api/web/index.html").read_text(encoding="utf-8")
        js = (ROOT / "api/web/app.js").read_text(encoding="utf-8")
        tags = Tags()
        tags.feed(html)
        self.assertEqual(len(tags.ids), len(set(tags.ids)))
        self.assertIn('id="settings-tab" type="button" hidden', html)
        self.assertIn('id="settings" hidden', html)
        self.assertIn(("div", "settings"), tags.ancestors["tariff-admin-form"])
        self.assertNotIn(("div", "operator"), tags.ancestors["tariff-admin-form"])
        self.assertIn(("div", "settings"), tags.ancestors["settings-stars-status"])
        self.assertIn("me.role!=='admin'", js)
        self.assertIn("showWorkspace('settings')", js)
        self.assertIn("/admin/payments/status", js)
        self.assertIn("renderTariffAdmin", js)
        self.assertIn("renderCoreReadiness", js)

    def test_no_secret_input_or_fake_payment_enabled(self):
        html = (ROOT / "api/web/index.html").read_text(encoding="utf-8")
        self.assertNotIn('name="yookassa_api_key"', html)
        self.assertNotIn('id="sbp-enable"', html)
        self.assertIn("СБП не подключена", html)
        self.assertIn("Telegram Stars", html)


if __name__ == "__main__":
    unittest.main()
