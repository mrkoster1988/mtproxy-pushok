"""v0.037 — ручные отчёты оператора, RBAC и честная оценка доказательств."""
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
from app.placement_verification import validate_observation_window

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000971")
OWNER = UUID("00000000-0000-4000-8000-000000000972")
OPERATOR = UUID("00000000-0000-4000-8000-000000000973")


class PlacementVerificationTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def operator(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {"id": OPERATOR, "role": "operator"}

    def owner(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": OWNER, "role": "advertiser"}

    def endpoint(self):
        return f"/api/v1/operator/campaigns/{CAMPAIGN}/display-observations"

    def body(self, result="visible"):
        return {"result": result, "method": "separate_unsubscribed_client",
                "observed_at": self.now.isoformat(),
                "note": "Проверено вручную на втором аккаунте"}

    def campaign(self, **changes):
        return {**{"id": CAMPAIGN, "moderation_status": "approved",
                   "payment_status": "verified", "placement_status": "scheduled",
                   "scheduled_start_at": self.now - timedelta(minutes=2),
                   "scheduled_end_at": self.now + timedelta(hours=1)}, **changes}

    def test_window_validation_and_stale_attestation(self):
        self.assertIsNone(validate_observation_window(
            self.now - timedelta(minutes=2), self.now + timedelta(hours=1), self.now, self.now))
        for observed in (self.now-timedelta(minutes=11), self.now+timedelta(minutes=2)):
            with self.subTest(observed=observed), self.assertRaisesRegex(ValueError, "observation_not_recent"):
                validate_observation_window(self.now-timedelta(hours=1),
                                            self.now+timedelta(hours=1), observed, self.now)
        with self.assertRaisesRegex(ValueError, "observation_timezone_required"):
            validate_observation_window(self.now-timedelta(minutes=2),
                                        self.now+timedelta(hours=1), datetime(2026, 11, 9), self.now)
        with self.assertRaisesRegex(ValueError, "observation_outside_placement_window"):
            validate_observation_window(self.now+timedelta(minutes=2),
                                        self.now+timedelta(hours=1), self.now, self.now)

    def test_operator_access_required_before_sql(self):
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.client.post(self.endpoint(), json=self.body()).status_code, 401)
            self.assertEqual(self.client.get(self.endpoint()).status_code, 401)
            db.assert_not_called()

    def test_invalid_payload_rejected(self):
        self.operator()
        for body in (
            {**self.body(), "method": "telegram_api"},
            {**self.body(), "result": "automatic"},
            {**self.body(), "note": "   short   "},
            {**self.body(), "observed_at": "2026-11-09T18:00:00"},
        ):
            with self.subTest(body=body), patch("app.main.psycopg.connect") as db:
                response = self.client.post(self.endpoint(), json=body)
                self.assertEqual(response.status_code, 422, response.text)
                db.assert_not_called()

    def test_recorded_report_has_audit_but_does_not_activate(self):
        self.operator()
        report = {"id": 14, "result": "visible", "method": "separate_unsubscribed_client",
                  "observed_at": self.now, "recorded_at": self.now}
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [self.campaign(), report]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(self.endpoint(), json=self.body())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["operator_report_only"])
        self.assertFalse(response.json()["telegram_api_verified"])
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertIn("placement_observations", calls[1].args[0])
        self.assertIn("placement_observation_recorded", calls[2].args[0])
        self.assertNotIn("UPDATE campaigns", str(calls))

    def test_active_accepts_removal_report_after_slot_end(self):
        self.operator()
        report = {"id": 15, "result": "not_visible", "method": "separate_unsubscribed_client",
                  "observed_at": self.now, "recorded_at": self.now}
        conn = MagicMock()
        active = self.campaign(placement_status="active",
                               scheduled_start_at=self.now-timedelta(hours=2),
                               scheduled_end_at=self.now-timedelta(hours=1))
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [active, report]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(self.endpoint(), json=self.body("not_visible"))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 3)

    def test_completed_cannot_record_observation(self):
        self.operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = self.campaign(placement_status="completed")
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(self.endpoint(), json=self.body("not_visible"))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_unpaid_or_unscheduled_does_not_create_observation(self):
        self.operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = self.campaign(payment_status="awaiting_payment")
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(self.endpoint(), json=self.body())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_public_latest_result_no_private_note(self):
        self.owner()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "id": CAMPAIGN, "result": "visible", "observed_at": self.now,
            "recorded_at": self.now}
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.get(f"/api/v1/campaigns/{CAMPAIGN}/display-observation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "visible")
        self.assertNotIn("note", response.json())
        query, params = conn.__enter__.return_value.execute.call_args.args
        self.assertIn("c.advertiser_user_id = %s", query)
        self.assertEqual(params, (CAMPAIGN, OWNER))

    def test_public_non_owner_404(self):
        self.owner()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.get(f"/api/v1/campaigns/{CAMPAIGN}/display-observation")
        self.assertEqual(response.status_code, 404)

    def test_activate_requires_latest_fresh_positive_observation(self):
        self.operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [None, {"id": CAMPAIGN}]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(f"/api/v1/operator/campaigns/{CAMPAIGN}/activate", json={
                "tag_applied_on_node": True, "mtproxybot_promotion_selected": True,
                "independent_client_checked": True,
                "verification_note": "Проверено на втором Telegram-клиенте вручную."
            })
        self.assertEqual(response.status_code, 409)
        update_sql = conn.__enter__.return_value.execute.call_args_list[0].args[0]
        self.assertIn("newest.id DESC", update_sql)
        self.assertIn("o.result = 'visible'", update_sql)
        self.assertIn("interval '10 minutes'", update_sql)
        self.assertIn("c.payment_status = 'verified'", update_sql)
        self.assertIn("proxy_sponsor_registrations", update_sql)
        self.assertIn("sc.telegram_channel_id IS NOT NULL", update_sql)

    def test_migration_and_install_hook(self):
        schema = (ROOT / "database/011_placement_observations.sql").read_text()
        self.assertIn("GENERATED ALWAYS AS IDENTITY", schema)
        self.assertIn("observer_user_id", schema)
        self.assertIn("result IN ('visible', 'not_visible')", schema)
        self.assertIn("011_placement_observations.sql", (ROOT / "setup.sh").read_text())


if __name__ == "__main__":
    unittest.main()
