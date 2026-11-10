"""PR #19: ручные подтверждения поверх независимых наблюдений показа."""
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

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000701")
OPERATOR = UUID("00000000-0000-4000-8000-000000000703")
PAYLOAD = {
    "tag_applied_on_node": True,
    "mtproxybot_promotion_selected": True,
    "independent_client_checked": True,
    "verification_note": "Наблюдение с отдельного Telegram-клиента подтверждено оператором.",
}


class ActivationAttestationTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def operator(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {
            "id": OPERATOR, "role": "operator"}

    def send(self, payload=PAYLOAD):
        return self.client.post(f"/api/v1/operator/campaigns/{CAMPAIGN}/activate", json=payload)

    def test_authentication_before_sql(self):
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.send().status_code, 401)
            db.assert_not_called()

    def test_missing_or_false_confirmations_rejected_before_db(self):
        self.operator()
        bad = [{}, {**PAYLOAD, "tag_applied_on_node": False},
               {**PAYLOAD, "mtproxybot_promotion_selected": False},
               {**PAYLOAD, "independent_client_checked": False},
               {**PAYLOAD, "verification_note": "short"},
               {**PAYLOAD, "verification_note": " " * 21},
               {**PAYLOAD, "verification_note": "x" * 501},
               {**PAYLOAD, "unexpected": True}]
        for value in bad:
            with self.subTest(value=value), patch("app.main.psycopg.connect") as db:
                self.assertEqual(self.send(value).status_code, 422)
                db.assert_not_called()

    def test_no_observation_means_no_activation(self):
        self.operator()
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.side_effect = [None, {"id": CAMPAIGN}]
        with patch("app.main.psycopg.connect", return_value=db):
            r = self.send()
        self.assertEqual(r.status_code, 409)
        sql = db.__enter__.return_value.execute.call_args_list[0].args[0]
        self.assertIn("newest.id DESC", sql)
        self.assertIn("o.result = 'visible'", sql)
        self.assertIn("o.observed_at >= now() - interval '10 minutes'", sql)
        self.assertIn("o.recorded_at >= now() - interval '10 minutes'", sql)
        self.assertIn("proxy_sponsor_registrations", sql)
        self.assertIn("sc.telegram_channel_id IS NOT NULL", sql)

    def test_success_audits_operator_without_telegram_claim(self):
        self.operator()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        row = {
            "id": CAMPAIGN, "channel_id": UUID(int=13), "tariff_id": UUID(int=14),
            "tariff_name_snapshot": "1 день", "duration_seconds_snapshot": 86400,
            "price_kopecks_snapshot": 30000, "currency_snapshot": "RUB",
            "moderation_status": "approved", "payment_status": "verified",
            "placement_status": "active", "requested_start_at": None,
            "scheduled_start_at": now, "scheduled_end_at": now,
            "moderator_comment": None,
        }
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.return_value = row
        with patch("app.main.psycopg.connect", return_value=db):
            r = self.send()
        self.assertEqual(r.status_code, 200, r.text)
        audit = db.__enter__.return_value.execute.call_args_list[1]
        self.assertIn("operator_attestation", audit.args[0])
        self.assertEqual(audit.args[1], (OPERATOR, CAMPAIGN, PAYLOAD["verification_note"]))
        self.assertNotIn("telegram_api_verified", audit.args[0])
