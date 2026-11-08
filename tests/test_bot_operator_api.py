"""API модерации Telegram: права, статусы, аудит и доставка уведомлений."""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
from app import main

SECRET = "a" * 64
OWNER = UUID("00000000-0000-4000-8000-000000000118")
CID = UUID("00000000-0000-4000-8000-000000000117")
ITEM = {
    "id": CID, "channel_title": "Мой канал",
    "channel_url": "https://t.me/mychannel",
    "price_kopecks_snapshot": 30000,
    "currency_snapshot": "RUB ", "created_at": datetime.now(timezone.utc),
}


def db(*responses):
    fake = MagicMock()
    connection = fake.__enter__.return_value
    results = []
    for row in responses:
        cur = MagicMock()
        cur.fetchone.return_value = row
        cur.fetchall.return_value = row if isinstance(row, list) else []
        results.append(cur)
    connection.execute.side_effect = results
    return fake


class BotOperatorApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.env = patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET})
        self.env.start()
        self.addCleanup(self.env.stop)

    def post(self, route, data=None, headers=None):
        return self.client.post(
            "/api/v1/internal/bot/operator/" + route,
            json={"telegram_user_id": 555, **(data or {})},
            headers=({"X-Pushok-Bot-Secret": SECRET}
                     if headers is None else headers),
        )

    def test_wrong_secret_rejected_before_database(self):
        with patch("app.bot_operator._db") as connector:
            r = self.post("queue", headers={"X-Pushok-Bot-Secret": "b" * 64})
        self.assertEqual(r.status_code, 401)
        connector.assert_not_called()

    def test_advertiser_cannot_see_moderation_queue(self):
        conn = db(None)
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("queue")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_operator_gets_pending_queue(self):
        conn = db({"id": OWNER, "role": "operator"}, [ITEM])
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("queue")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.json()[0]["currency_snapshot"], "RUB")
        self.assertEqual(len(r.json()), 1)
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(calls[0].args[1], (555,))
        self.assertIn("role IN ('operator', 'admin')", calls[0].args[0])
        self.assertIn("moderation_status = 'pending'", calls[1].args[0])
        self.assertIn("LIMIT 5", calls[1].args[0])

    def test_approved_status_sets_waiting_payment_and_audit(self):
        conn = db(
            {"id": OWNER, "role": "admin"},
            {"id": CID, "moderation_status": "approved",
             "payment_status": "awaiting_payment"},
            None,
        )
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("review", {"campaign_id": str(CID),
                                      "decision": "approved"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["payment_status"], "awaiting_payment")
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertIn("moderation_status = 'pending'", calls[1].args[0])
        self.assertIn("payment_status = 'not_required'", calls[1].args[0])
        self.assertIn("campaign_moderated", calls[2].args[0])
        self.assertEqual(calls[2].args[1][0], OWNER)

    def test_comment_required_for_rejection(self):
        with patch("app.bot_operator._db") as connector:
            r = self.post("review", {"campaign_id": str(CID),
                                      "decision": "rejected",
                                      "comment": "   "})
        self.assertEqual(r.status_code, 422)
        connector.assert_not_called()

    def test_return_for_changes_records_reason(self):
        conn = db(
            {"id": OWNER, "role": "operator"},
            {"id": CID, "moderation_status": "changes_requested",
             "payment_status": "not_required"},
            None,
        )
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("review", {"campaign_id": str(CID),
                                      "decision": "changes_requested",
                                      "comment": "  Исправьте ссылку  "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            conn.__enter__.return_value.execute.call_args_list[1].args[1][2],
            "Исправьте ссылку",
        )

    def test_stale_request_no_second_audit_event(self):
        conn = db({"id": OWNER, "role": "admin"}, None)
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("review", {"campaign_id": str(CID), "decision": "approved"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 2)
        self.assertIsNotNone(conn.__exit__.call_args.args[0])

    def test_notification_after_send_ack_is_idempotent(self):
        conn = db({"id": OWNER, "role": "admin"}, {"campaign_id": CID})
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("notifications/ack", {"campaign_id": str(CID)})
        self.assertEqual(r.status_code, 200, r.text)
        sql_text = conn.__enter__.return_value.execute.call_args_list[1].args[0]
        self.assertIn("ON CONFLICT (campaign_id, operator_user_id) DO NOTHING", sql_text)

    def test_notification_queue_checks_role_and_pending_status(self):
        conn = db({"id": OWNER, "role": "admin"}, [ITEM])
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("notifications")
        self.assertEqual(r.status_code, 200, r.text)
        statement, params = conn.__enter__.return_value.execute.call_args_list[1].args
        self.assertIn("n.campaign_id IS NULL", statement)
        self.assertIn("moderation_status = 'pending'", statement)
        self.assertEqual(params, (OWNER,))

    def test_operator_history_only_own_decisions(self):
        event = {
            "campaign_id": CID, "channel_title": "Мой канал",
            "decision": "rejected", "created_at": datetime.now(timezone.utc),
        }
        conn = db({"id": OWNER, "role": "operator"}, [event])
        with patch("app.bot_operator._db", return_value=conn):
            r = self.post("history")
        self.assertEqual(r.status_code, 200, r.text)
        sql_query, params = conn.__enter__.return_value.execute.call_args_list[1].args
        self.assertIn("a.actor_user_id = %s", sql_query)
        self.assertEqual(params, (OWNER,))


if __name__ == "__main__":
    unittest.main()
