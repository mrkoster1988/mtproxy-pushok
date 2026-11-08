"""Контракт защищённого API очереди уведомлений рекламодателю."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

PATH = "/api/v1/internal/bot/advertisers/outbox/"
SECRET = "a" * 64
NOTICE_ID = "00000000-0000-4000-8000-000000000171"
TOKEN = "00000000-0000-4000-8000-000000000172"
CAMPAIGN = "00000000-0000-4000-8000-000000000173"


def fake_connection(rows):
    connection = MagicMock()
    execute = connection.__enter__.return_value.execute
    execute.return_value.fetchall.return_value = rows
    execute.return_value.fetchone.return_value = rows[0] if rows else None
    return connection


class AdvertiserOutboxApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.secret = patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET})
        self.secret.start()
        self.addCleanup(self.secret.stop)

    @property
    def headers(self):
        return {"X-Pushok-Bot-Secret": SECRET}

    def test_claim_requires_secret_before_sql(self):
        with patch("app.bot_advertiser_notices.psycopg.connect") as connect:
            response = self.client.post(PATH + "claim")
        self.assertEqual(response.status_code, 401)
        connect.assert_not_called()

    def test_claim_returns_only_leased_recipient_and_snapshot(self):
        row = {
            "notice_id": UUID(NOTICE_ID), "claim_token": UUID(TOKEN),
            "campaign_id": UUID(CAMPAIGN), "telegram_user_id": 93455,
            "decision": "changes_requested", "comment": "Исправьте описание",
        }
        connection = fake_connection([row])
        with patch("app.bot_advertiser_notices.psycopg.connect",
                   return_value=connection):
            response = self.client.post(PATH + "claim", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()[0]["telegram_user_id"], 93455)
        self.assertEqual(response.json()[0]["claim_token"], TOKEN)
        sql = connection.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("LIMIT 5", sql)
        self.assertIn("lease_until", sql)
        self.assertIn("next_attempt_at", sql)
        self.assertIn("delivered_at IS NULL", sql)

    def test_finish_rejects_wrong_claim_token(self):
        connection = fake_connection([])
        with patch("app.bot_advertiser_notices.psycopg.connect",
                   return_value=connection):
            response = self.client.post(PATH + "finish", headers=self.headers,
                                        json={"notice_id": NOTICE_ID,
                                              "claim_token": TOKEN})
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(connection.__exit__.call_args.args[0])

    def test_finish_success_and_permanent_failure(self):
        for permanent in (False, True):
            with self.subTest(permanent=permanent):
                connection = fake_connection([{"id": UUID(NOTICE_ID)}])
                with patch("app.bot_advertiser_notices.psycopg.connect",
                           return_value=connection):
                    response = self.client.post(PATH + "finish",
                                                headers=self.headers, json={
                                                    "notice_id": NOTICE_ID,
                                                    "claim_token": TOKEN,
                                                    "permanent_failure": permanent,
                                                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"acknowledged": True})
                sql, args = connection.__enter__.return_value.execute.call_args.args
                self.assertIn("claim_token = %s", sql)
                self.assertIn("lease_until >= now()", sql)
                self.assertEqual(args, (permanent, permanent, UUID(NOTICE_ID), UUID(TOKEN)))

    def test_outbox_routes_hidden_from_openapi(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any("advertisers/outbox" in path
                             for path in response.json()["paths"]))


if __name__ == "__main__":
    unittest.main()
