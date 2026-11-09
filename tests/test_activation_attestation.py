"""v0.032 — ручная аттестация спонсора и безопасные проверки API."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000701")
OPERATOR = UUID("00000000-0000-4000-8000-000000000703")
PAYLOAD = {
    "tag_applied_on_node": True,
    "mtproxybot_promotion_selected": True,
    "independent_client_checked": True,
    "verification_note": "Проверено с отдельного клиента Telegram после настройки спонсора.",
}


def fake_db(*rows):
    db = MagicMock()
    db.__enter__.return_value.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row}) for row in rows
    ]
    return db


class ActivationAttestationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def grant_operator(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {
            "id": OPERATOR, "role": "operator",
        }

    def send(self, payload=PAYLOAD):
        return self.client.post(
            f"/api/v1/operator/campaigns/{CAMPAIGN}/activate", json=payload,
        )

    def test_requires_operator_auth_before_sql(self):
        with patch("app.main.psycopg.connect") as db:
            result = self.send()
        self.assertEqual(result.status_code, 401)
        db.assert_not_called()

    def test_missing_false_or_invalid_confirmation_rejected_without_sql(self):
        self.grant_operator()
        bad = [
            {}, {**PAYLOAD, "tag_applied_on_node": False},
            {**PAYLOAD, "mtproxybot_promotion_selected": False},
            {**PAYLOAD, "independent_client_checked": False},
            {**PAYLOAD, "verification_note": "    "},
            {**PAYLOAD, "verification_note": "Слишком кратко"},
            {**PAYLOAD, "verification_note": "x" * 501},
            {**PAYLOAD, "extra_field": True},
        ]
        for payload in bad:
            with self.subTest(payload=payload), patch("app.main.psycopg.connect") as db:
                self.assertEqual(self.send(payload).status_code, 422)
                db.assert_not_called()

    def test_atomic_sql_guards_ready_conditions(self):
        self.grant_operator()
        db = fake_db(None, {"exists": True})
        with patch("app.main.psycopg.connect", return_value=db):
            response = self.send()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "placement_not_ready_or_outside_window")
        queries = db.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(queries), 2)
        sql = queries[0].args[0]
        for item in ("c.moderation_status = 'approved'", "c.payment_status = 'verified'",
                     "n.status = 'active'", "sc.telegram_channel_id IS NOT NULL",
                     "sr.proxy_node_id = n.id", "sr.engine = n.target_engine",
                     "c.scheduled_start_at <= now()", "c.scheduled_end_at > now()"):
            self.assertIn(item, sql)

    def test_audit_records_operator_not_telegram_telemetry(self):
        self.grant_operator()
        now = datetime.now(timezone.utc)
        row = {
            "id": CAMPAIGN, "channel_id": UUID(int=13), "tariff_id": UUID(int=14),
            "tariff_name_snapshot": "1 день", "duration_seconds_snapshot": 86400,
            "price_kopecks_snapshot": 30000, "currency_snapshot": "RUB",
            "moderation_status": "approved", "payment_status": "verified",
            "placement_status": "active", "requested_start_at": None,
            "scheduled_start_at": now - timedelta(minutes=1),
            "scheduled_end_at": now + timedelta(hours=1),
            "moderator_comment": None,
        }
        db = fake_db(row, None)
        with patch("app.main.psycopg.connect", return_value=db):
            response = self.send()
        self.assertEqual(response.status_code, 200, response.text)
        queries = db.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(queries), 2)
        self.assertIn("operator_attestation", queries[1].args[0])
        self.assertEqual(queries[1].args[1], (OPERATOR, CAMPAIGN, PAYLOAD["verification_note"]))
        self.assertNotIn("telegram_verified", queries[1].args[0])
        self.assertNotIn("ad_tag", str(queries[1].args))


if __name__ == "__main__":
    unittest.main()
