"""v0.048 — бот читает текущий тариф под внутренним секретом."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

SECRET = "a" * 64
PATH = "/api/v1/internal/bot/tariff-quote"


class BotTariffQuoteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()

    def test_requires_secret_before_db(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as db:
                response = self.client.post(PATH)
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_current_price_is_loaded_not_hardcoded(self):
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "name": "3 дня", "duration_seconds": 259200,
            "price_kopecks": 70000, "currency": "RUB ", "stars_price_xtr": 350
        }
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=conn):
                response = self.client.post(PATH, headers={"X-Pushok-Bot-Secret": SECRET}, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["price_kopecks"], 70000)
        self.assertEqual(response.json()["stars_price_xtr"], 350)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_no_active_tariff_is_409(self):
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=conn):
                response = self.client.post(PATH, headers={"X-Pushok-Bot-Secret": SECRET}, json={})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
