"""Проверка API черновиков: фиксированный тариф, секрет, изоляция, повторы."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)

from app import main

SECRET = "a" * 64
PATH = "/api/v1/internal/bot/campaign-drafts"
USER_ID = "00000000-0000-4000-8000-000000000099"
CHANNEL_ID = "00000000-0000-4000-8000-000000000098"
CAMPAIGN_ID = "00000000-0000-4000-8000-000000000097"
KEY = "00000000-0000-4000-8000-000000000096"
TARIFF_ID = "00000000-0000-4000-8000-000000000001"


class DBResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDB:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []
        self.exited_error = None

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_):
        self.exited_error = error_type
        return False

    def execute(self, query, values):
        self.calls.append((query, values))
        return DBResult(next(self.rows))


def fake_db(created=True, owner=USER_ID, channel=CHANNEL_ID):
    tariff = {
        "id": TARIFF_ID, "name": "1 день", "duration_seconds": 86400,
        "price_kopecks": 30000, "currency": "RUB ",
    }
    return FakeDB([
        tariff,
        None, {"id": USER_ID},
        None, {"id": CHANNEL_ID},
        {"id": CAMPAIGN_ID} if created else None,
        *([] if created else [{
            "id": CAMPAIGN_ID, "advertiser_user_id": owner, "channel_id": channel
        }]),
    ])


class DraftAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def request(self, headers=None, payload=None):
        return self.client.post(PATH, headers=headers or {"X-Pushok-Bot-Secret": SECRET},
                                json=payload or {
                                    "telegram_user_id": 123,
                                    "channel_username": "MyChannel",
                                    "request_key": KEY,
                                })

    def test_create_draft_for_exact_one_day_tariff(self):
        db = fake_db()
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=db):
                response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["campaign_id"], CAMPAIGN_ID)
        self.assertEqual(payload["channel_url"], "https://t.me/mychannel")
        self.assertEqual(payload["price_kopecks"], 30000)
        self.assertEqual(payload["moderation_status"], "draft")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(db.calls[1][1], (123,))
        self.assertEqual(db.calls[3][1], (USER_ID, "https://t.me/mychannel", "@mychannel"))
        self.assertIn("bot_request_key", db.calls[5][0])
        self.assertIn("ON CONFLICT (bot_request_key)", db.calls[5][0])
        self.assertEqual(db.calls[5][1][-1].hex, KEY.replace("-", ""))

    def test_repeated_key_returns_same_id(self):
        db = fake_db(created=False)
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=db):
                response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["campaign_id"], CAMPAIGN_ID)
        self.assertIn("bot_request_key = %s", db.calls[-1][0])

    def test_reuse_key_for_foreign_campaign_refused(self):
        db = fake_db(created=False, owner="00000000-0000-4000-8000-000000000011")
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=db):
                response = self.request()
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.exited_error)

    def test_no_tariff_stops_before_user_creation(self):
        db = FakeDB([None])
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect", return_value=db):
                response = self.request()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(db.calls), 1)
        self.assertIn("is_active = TRUE", db.calls[0][0])
        self.assertIn("price_kopecks = 30000", db.calls[0][0])

    def test_secret_checked_before_db_access(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                response = self.request(headers={"X-Pushok-Bot-Secret": "b" * 64})
        self.assertEqual(response.status_code, 401)
        connect.assert_not_called()

    def test_invalid_username_refused_without_db_access(self):
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET}):
            with patch("app.bot_routes.psycopg.connect") as connect:
                response = self.request(payload={
                    "telegram_user_id": 123,
                    "channel_username": "https://evil.com/malicious",
                    "request_key": KEY,
                })
        self.assertEqual(response.status_code, 422)
        connect.assert_not_called()

    def test_migration_is_repeatable_and_non_destructive(self):
        migration = (ROOT / "database/003_bot_draft_idempotency.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS bot_request_key UUID", migration)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS", migration)
        self.assertIn("WHERE bot_request_key IS NOT NULL", migration)


if __name__ == "__main__":
    unittest.main()
