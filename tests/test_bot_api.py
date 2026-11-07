"""Тесты закрытого read-only API Telegram-бота."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)

from app import main

SECRET = "b" * 64
PATH = "/api/v1/internal/bot/campaigns"


class BotCampaignApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_secret_missing_returns_503_before_database(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": ""}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                response = self.client.post(PATH, json={"telegram_user_id": 123})
        self.assertEqual(response.status_code, 503)
        connect.assert_not_called()

    def test_placeholder_secret_is_rejected(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": "REPLACE_WITH_PLACEHOLDER_000000000000000"}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                response = self.client.post(PATH, json={"telegram_user_id": 123})
        self.assertEqual(response.status_code, 503)
        connect.assert_not_called()

    def test_wrong_secret_returns_401_without_db(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                response = self.client.post(
                    PATH, json={"telegram_user_id": 123},
                    headers={"X-Pushok-Bot-Secret": "c" * 64},
                )
        self.assertEqual(response.status_code, 401)
        connect.assert_not_called()

    def test_invalid_telegram_user_id(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            response = self.client.post(
                PATH, json={"telegram_user_id": -1},
                headers={"X-Pushok-Bot-Secret": SECRET},
            )
        self.assertEqual(response.status_code, 422)

    def test_only_requested_telegram_user_campaigns(self):
        row = {
            "channel_title": "Тестовый канал",
            "tariff_name_snapshot": "1 день",
            "price_kopecks_snapshot": 30000,
            "currency_snapshot": "RUB ",
            "moderation_status": "approved",
            "payment_status": "verified",
            "placement_status": "scheduled",
            "scheduled_start_at": None,
        }
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                db = connect.return_value.__enter__.return_value
                db.execute.return_value.fetchall.return_value = [row]
                response = self.client.post(
                    PATH, json={"telegram_user_id": 123},
                    headers={"X-Pushok-Bot-Secret": SECRET},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()[0]["channel_title"], "Тестовый канал")
        self.assertEqual(response.json()[0]["currency_snapshot"], "RUB")
        sql_query, params = db.execute.call_args.args
        self.assertIn("WHERE u.telegram_user_id = %s", sql_query)
        self.assertIn("LIMIT 10", sql_query)
        self.assertEqual(params, (123,))

    def test_user_with_no_campaigns_gets_empty_list(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
                response = self.client.post(
                    PATH, json={"telegram_user_id": 789},
                    headers={"X-Pushok-Bot-Secret": SECRET},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
