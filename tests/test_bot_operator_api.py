"""Unit API: операторская очередь и решение без доверия к Telegram username."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
from app import main

SECRET = "b" * 64
CID = "00000000-0000-4000-8000-000000000123"
UID = UUID("00000000-0000-4000-8000-000000000124")
PREFIX = "/api/v1/internal/bot/operators/"


def fake_conn(*rows):
    conn = MagicMock()
    conn.__enter__.return_value.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row, "fetchall.return_value": row})
        for row in rows
    ]
    return conn


class OperatorApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.env = patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": SECRET,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    @property
    def headers(self):
        return {"X-Pushok-Bot-Secret": SECRET}

    def test_missing_secret_does_not_query_database(self):
        with patch("app.bot_operator.psycopg.connect") as db:
            response = self.client.post(PREFIX + "queue",
                                        json={"telegram_user_id": 12})
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_non_operator_cannot_list_queue(self):
        db = fake_conn(None)
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "queue", headers=self.headers,
                json={"telegram_user_id": 321, "subscribe": True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)
        sql, args = db.__enter__.return_value.execute.call_args.args
        self.assertIn("role IN ('admin', 'operator')", sql)
        self.assertEqual(args, (321,))

    def test_queue_includes_only_pending_and_opt_in(self):
        item = {
            "campaign_id": UUID(CID), "channel_title": "Пример",
            "channel_url": "https://t.me/examplechannel",
            "tariff_name": "1 день", "price_kopecks": 30000, "currency": "RUB"
        }
        db = fake_conn({"id": UID}, None, {"ok": 1}, [item])
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "queue", headers=self.headers,
                json={"telegram_user_id": 321, "subscribe": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["subscribed"])
        self.assertEqual(response.json()["pending"][0]["campaign_id"], CID)
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertIn("bot_operator_subscriptions", calls[1].args[0])
        self.assertIn("moderation_status = 'pending'", calls[3].args[0])
        self.assertIn("LIMIT 5", calls[3].args[0])

    def test_decision_requires_operator_role(self):
        db = fake_conn(None)
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "decide", headers=self.headers,
                json={"telegram_user_id": 321,
                      "campaign_id": CID, "decision": "approved"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)

    def test_rejection_requires_comment_before_db(self):
        with patch("app.bot_operator.psycopg.connect") as db:
            response = self.client.post(
                PREFIX + "decide", headers=self.headers,
                json={"telegram_user_id": 321, "campaign_id": CID,
                      "decision": "rejected", "comment": "   "})
        self.assertEqual(response.status_code, 422)
        db.assert_not_called()

    def test_approved_sets_awaiting_payment_and_audit(self):
        row = {"campaign_id": UUID(CID), "moderation_status": "approved",
               "payment_status": "awaiting_payment"}
        db = fake_conn({"id": UID}, row, None)
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "decide", headers=self.headers,
                json={"telegram_user_id": 321, "campaign_id": CID,
                      "decision": "approved"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["payment_status"], "awaiting_payment")
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertIn("moderation_status = 'pending'", calls[1].args[0])
        self.assertIn("campaign_moderated", calls[2].args[0])
        self.assertEqual(calls[2].args[1][0], UID)

    def test_repeat_fails_without_extra_audit(self):
        db = fake_conn({"id": UID}, None)
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "decide", headers=self.headers,
                json={"telegram_user_id": 321, "campaign_id": CID,
                      "decision": "approved"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 2)
        self.assertIsNotNone(db.__exit__.call_args.args[0])

    def test_recipients_always_recheck_role(self):
        db = fake_conn([(321,), (432,)])
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(PREFIX + "recipients", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [321, 432])
        sql = db.__enter__.return_value.execute.call_args.args[0]
        self.assertIn("u.role IN ('admin', 'operator')", sql)
        self.assertIn("LIMIT 5", sql)

    def test_unsubscribe_by_telegram_id(self):
        db = fake_conn(None)
        with patch("app.bot_operator.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "unsubscribe", headers=self.headers,
                json={"telegram_user_id": 321})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"subscribed": False})
        self.assertIn("DELETE FROM bot_operator_subscriptions",
                      db.__enter__.return_value.execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
