"""Юнит-тесты закрытого outbox: авторизация, claim, повторная отправка, ACK."""
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
from app import main

SECRET = "f" * 64
NOTICE_ID = UUID("00000000-0000-4000-8000-000000000243")
CAMPAIGN_ID = UUID("00000000-0000-4000-8000-000000000244")
PREFIX = "/api/v1/internal/bot/operators/outbox/"


def fake_db(*rows):
    connection = MagicMock()
    cursor = connection.__enter__.return_value.execute
    cursor.side_effect = [
        MagicMock(**{
            "fetchone.return_value": row,
            "fetchall.return_value": row if isinstance(row, list) else [],
        }) for row in rows
    ]
    return connection


class OutboxAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.patch = patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.headers = {"X-Pushok-Bot-Secret": SECRET}

    def test_unauthorized_claim_never_reaches_database(self):
        with patch("app.bot_outbox.psycopg.connect") as connect:
            response = self.client.post(PREFIX + "claim")
        self.assertEqual(response.status_code, 401)
        connect.assert_not_called()

    def test_claim_returns_bounded_and_role_filtered_notices(self):
        notices = [{"notice_id": NOTICE_ID, "campaign_id": CAMPAIGN_ID,
                    "telegram_user_id": 123}]
        conn = fake_db(notices, None)
        with patch("app.bot_outbox.psycopg.connect", return_value=conn):
            response = self.client.post(PREFIX + "claim", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()[0]["notice_id"], str(NOTICE_ID))
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertIn("u.role IN ('admin', 'operator')", calls[0].args[0])
        self.assertIn("bot_operator_subscriptions", calls[0].args[0])
        self.assertIn("FOR UPDATE OF n SKIP LOCKED", calls[0].args[0])
        self.assertIn("LIMIT 5", calls[0].args[0])
        self.assertIn("attempts = attempts + 1", calls[1].args[0])
        self.assertEqual(calls[1].args[1][0], [NOTICE_ID])

    def test_empty_queue_has_no_claim_update(self):
        conn = fake_db([])
        with patch("app.bot_outbox.psycopg.connect", return_value=conn):
            response = self.client.post(PREFIX + "claim", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_ack_sets_delivered_at_after_telegram_send(self):
        conn = fake_db({"id": NOTICE_ID})
        with patch("app.bot_outbox.psycopg.connect", return_value=conn):
            response = self.client.post(
                PREFIX + "ack", headers=self.headers,
                json={"notice_id": str(NOTICE_ID)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["acknowledged"])
        sql = conn.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("SET delivered_at = now()", sql)

    def test_repeated_ack_is_safe(self):
        conn = fake_db(None, {"exists": 1})
        with patch("app.bot_outbox.psycopg.connect", return_value=conn):
            response = self.client.post(
                PREFIX + "ack", headers=self.headers,
                json={"notice_id": str(NOTICE_ID)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 2)

    def test_unknown_notice_is_not_acknowledged(self):
        conn = fake_db(None, None)
        with patch("app.bot_outbox.psycopg.connect", return_value=conn):
            response = self.client.post(
                PREFIX + "ack", headers=self.headers,
                json={"notice_id": str(NOTICE_ID)})
        self.assertEqual(response.status_code, 404)

    def test_invalid_notice_id_is_rejected_without_database_access(self):
        with patch("app.bot_outbox.psycopg.connect") as connect:
            response = self.client.post(
                PREFIX + "ack", headers=self.headers,
                json={"notice_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 422)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
