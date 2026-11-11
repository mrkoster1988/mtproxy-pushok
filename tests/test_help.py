"""Справка доступна без учётной записи и содержит рабочую навигацию."""
import os
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
sys.path.insert(0, str(ROOT / "api"))
from fastapi.testclient import TestClient
from app.main import app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.summaries = 0
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "summary":
            self.summaries += 1


class HelpTests(unittest.TestCase):
    def test_help_is_public_and_does_not_require_database(self):
        with TestClient(app) as client:
            response = client.get("/assets/help.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Как разместить канал", response.text)

    def test_help_navigation_targets_exist_and_ids_are_unique(self):
        page = Links()
        page.feed((ROOT / "api/web/help.html").read_text())
        self.assertEqual(len(page.ids), len(set(page.ids)))
        for link in page.links:
            if link.startswith("#"):
                self.assertIn(link[1:], page.ids)
        for section in ["start", "channels", "calendar", "payments", "statuses", "operator", "settings", "install", "errors"]:
            self.assertIn(section, page.ids)

    def test_context_links_resolve_to_help_sections(self):
        index, help_page = Links(), Links()
        index.feed((ROOT / "api/web/index.html").read_text())
        help_page.feed((ROOT / "api/web/help.html").read_text())
        self.assertGreaterEqual(index.summaries, 4)
        self.assertIn("/assets/help.html", index.links)
        links = [link for link in index.links if link.startswith("/assets/help.html#")]
        self.assertGreaterEqual(len(links), 4)
        for link in links:
            self.assertIn(link.split("#")[1], help_page.ids)

    def test_manual_steps_and_payment_limits_are_explained(self):
        guide = (ROOT / "api/web/help.html").read_text()
        for term in ["15 минут", "XTR", "5 МБ", "не подтверждает поступление", "не выполняет эти действия", "не является подтверждённым размещением"]:
            self.assertIn(term, guide)
        self.assertNotIn("<script", guide)
