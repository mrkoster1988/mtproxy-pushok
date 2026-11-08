"""API диагностики: секрет, актуальная роль admin и аудит ручных повторов."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID
from datetime import datetime, timezone

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

SECRET = "c" * 64
PREFIX = "/api/v1/internal/bot/admin/"
ADMIN_UUID = UUID("00000000-0000-4000-8000-000000000041")
NOTICE_UUID = UUID("00000000-0000-4000-8000-000000000042")
CAMPAIGN_UUID = UUID("00000000-0000-4000-8000-000000000043")


def fake_connection(*rows):
    conn = MagicMock()
    conn.__enter__.return_value.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row, "fetchall.return_value": row})
        for row in rows
    ]
    return conn


class DeliveryAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.env = patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET})
        self.env.start()
        self.addCleanup(self.env.stop)

    @property
    def headers(self):
        return {"X-Pushok-Bot-Secret": SECRET}

    def test_missing_or_wrong_secret_refused_before_database(self):
        for headers in ({}, {"X-Pushok-Bot-Secret": "d" * 64}):
            with self.subTest(headers=headers):
                with patch("app.bot_delivery_admin.psycopg.connect") as connect:
                    response = self.client.post(
                        PREFIX + "delivery-report", json={"telegram_user_id": 123},
                        headers=headers,
                    )
                self.assertEqual(response.status_code, 401)
                connect.assert_not_called()

    def test_operator_cannot_see_private_report(self):
        db = fake_connection(None)
        with patch("app.bot_delivery_admin.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "delivery-report", headers=self.headers,
                json={"telegram_user_id": 123},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)
        sql, params = db.__enter__.return_value.execute.call_args.args
        self.assertIn("role = 'admin'", sql)
        self.assertEqual(params, (123,))

    def test_admin_report_has_no_recipient_personal_data(self):
        counts = {"waiting": 2, "in_flight": 1, "delivered": 7, "abandoned": 3}
        op_counts = {"waiting": 4, "in_flight": 0, "delivered": 6}
        row = {
            "notice_id": NOTICE_UUID, "campaign_id": CAMPAIGN_UUID,
            "decision": "rejected", "attempts": 4, "status": "waiting",
            "next_attempt_at": datetime(2026, 11, 8, tzinfo=timezone.utc),
        }
        db = fake_connection({"id": ADMIN_UUID}, counts, op_counts, [row])
        with patch("app.bot_delivery_admin.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "delivery-report", headers=self.headers,
                json={"telegram_user_id": 123},
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["advertiser"]["abandoned"], 3)
        self.assertEqual(data["operator"]["waiting"], 4)
        self.assertEqual(data["recent_undelivered"][0]["notice_id"], str(NOTICE_UUID))
        self.assertNotIn("telegram_user_id", response.text)
        self.assertNotIn("comment", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertIn("FROM bot_operator_notice_outbox", calls[2].args[0])
        self.assertIn("LIMIT 8", calls[3].args[0])

    def test_retry_only_after_admin_check_and_with_audit(self):
        db = fake_connection({"id": ADMIN_UUID}, {"campaign_id": CAMPAIGN_UUID}, None)
        with patch("app.bot_delivery_admin.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "retry-advertiser-notice", headers=self.headers,
                json={"telegram_user_id": 123, "notice_id": str(NOTICE_UUID)},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"queued": True})
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertIn("delivered_at IS NULL", calls[1].args[0])
        self.assertIn("abandoned_at IS NULL", calls[1].args[0])
        self.assertIn("lease_until", calls[1].args[0])
        self.assertIn("next_attempt_at > now()", calls[1].args[0])
        self.assertIn("advertiser_notice_retry_requested", calls[2].args[0])
        self.assertEqual(calls[2].args[1][0], ADMIN_UUID)

    def test_retry_rejected_when_in_flight_delivered_or_blocked(self):
        db = fake_connection({"id": ADMIN_UUID}, None)
        with patch("app.bot_delivery_admin.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "retry-advertiser-notice", headers=self.headers,
                json={"telegram_user_id": 123, "notice_id": str(NOTICE_UUID)},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 2)
        self.assertIsNotNone(db.__exit__.call_args.args[0])

    def test_retry_foreign_role_denied_before_mutation(self):
        db = fake_connection(None)
        with patch("app.bot_delivery_admin.psycopg.connect", return_value=db):
            response = self.client.post(
                PREFIX + "retry-advertiser-notice", headers=self.headers,
                json={"telegram_user_id": 321, "notice_id": str(NOTICE_UUID)},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)

    def test_routes_not_in_public_openapi(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any("/admin/delivery-report" in p
                             for p in response.json()["paths"]))


if __name__ == "__main__":
    unittest.main()
