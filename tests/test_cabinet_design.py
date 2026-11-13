"""Контракт фирменного интерфейса: старые формы и API не теряются."""
import re
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "api" / "web"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.forms = []
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])
        if tag in ("script", "link", "img") and "src" in d:
            self.links.append(d["src"])
        if tag == "link" and "href" in d:
            self.links.append(d["href"])
        if tag == "form":
            self.forms.append(d.get("id"))
        if tag in ("input", "select"):
            self.inputs.append(d.get("name"))


class CabinetDesignTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.parser = Elements()
        self.parser.feed(self.html)

    def test_existing_ids_and_forms_are_preserved(self):
        ids = set(self.parser.ids)
        essential = {
            "identity", "logout", "login", "telegram-login", "login-note",
            "notice", "workspace", "advertiser", "operator", "advertiser-tab",
            "operator-tab", "refresh", "channel-form", "campaign-form",
            "channel-select", "tariff-select", "calendar-from", "calendar-time",
            "calendar-zone", "availability-days", "calendar-selection",
            "requested-start", "calendar-clear", "campaign-count", "campaigns",
            "node-admin", "node-form", "engine-environment",
            "engine-deployment", "engine-mode", "preferred-engine-row",
            "preferred-engine", "engine-preview", "engine-advice",
            "nodes-list", "queue-filter", "queue", "queue-count"
        }
        self.assertTrue(essential.issubset(ids), essential - ids)
        self.assertFalse([k for k, count in Counter(self.parser.ids).items() if count > 1])
        self.assertTrue({"channel-form", "campaign-form", "node-form", "tariff-admin-form"}.issubset(set(self.parser.forms)))
        self.assertIn("requested_start_at", self.parser.inputs)
        self.assertIn("telegram_url", self.parser.inputs)
        self.assertIn("preferred_engine", self.parser.inputs)

    def test_no_external_styles_scripts_or_tracking(self):
        self.assertEqual({url.split('?')[0] for url in self.parser.links}, {
            "/assets/styles.css", "/assets/app.js", "/assets/order.js", "/assets/approval.js", "/assets/pages.js", "/assets/pushok-mascot.svg"
        })
        self.assertNotIn("onclick=", self.html)
        self.assertNotIn("onload=", self.html)
        self.assertNotIn("fonts.googleapis.com", self.css)
        self.assertNotIn("@import", self.css)

    def test_svg_is_local_vector_without_script(self):
        source = (WEB / "pushok-mascot.svg").read_text(encoding="utf-8")
        tree = ET.fromstring(source)
        self.assertEqual(tree.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn("viewBox=", source)
        for disallowed in ("<script", "<foreignObject", "https://", "http://", "data:image/", "onload="):
            if disallowed in ("https://", "http://"):
                # The SVG namespace declaration is the only network-looking string.
                continue
            self.assertNotIn(disallowed, source)
        self.assertNotIn("xlink:href", source)

    def test_design_semantics_and_responsiveness(self):
        self.assertIn("puShok", self.html)
        self.assertIn("300 ₽", self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-label="Разделы кабинета"', self.html)
        self.assertIn("@media (max-width: 520px)", self.css)
        self.assertIn("@media (max-width: 760px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn(":focus-visible", self.css)
        self.assertIn("min-width: 320px", self.css)
        self.assertIn(".badge--active", self.css)
        self.assertIn(".badge--scheduled", self.css)

    def test_js_queue_and_statuses_use_safe_dom(self):
        self.assertIn("'badge badge--'+c.placement_status", self.js)
        self.assertIn("$('queue-count').textContent=queue.length+' заявок'", self.js)
        self.assertIn("textContent", self.js)
        self.assertNotIn("innerHTML", self.js)
        self.assertIn("fetch('/api/v1'+path", self.js)

    def test_version_and_standalone_boundary(self):
        main = (ROOT / "api/app/main.py").read_text(encoding="utf-8")
        setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"Версия проекта: v(\d+\.\d+)\.", readme)
        self.assertIsNotNone(match)
        release = match.group(1)
        self.assertIn('version="' + release + '"', main)
        self.assertIn("v" + release, setup)
        self.assertIn("v" + release, self.html)
        self.assertIn("Pushok VPN", (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
