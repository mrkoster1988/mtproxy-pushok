"""v0.040 — публичный отчёт владельца, приватные данные не раскрываются."""
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
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main
from app.placement_report import build_campaign_report, PUBLIC_EVENTS

CID = UUID("00000000-0000-4000-8000-000000000995")
UID = UUID("00000000-0000-4000-8000-000000000996")
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)

def campaign():
    return {
        "id": CID, "channel_title": "Тестовый канал",
        "channel_url": "https://t.me/pushok_test",
        "tariff_name_snapshot": "1 день",
        "duration_seconds_snapshot": 86400, "price_kopecks_snapshot": 30000,
        "currency_snapshot": "RUB",
        "moderation_status": "approved", "payment_status": "verified",
        "placement_status": "completed", "requested_start_at": NOW,
        "scheduled_start_at": NOW, "scheduled_end_at": NOW,
        "owner_secret": "NEVER_EXPORT",
    }

class CampaignReportTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.path = f"/api/v1/campaigns/{CID}/report"

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def login(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": UID, "role": "advertiser"}

    def test_authentication_precedes_db(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_pure_report_whitelist_and_no_impression_claims(self):
        observation = {
            "visible_count": 2, "not_visible_count": 1,
            "private_screenshot_count": 1, "latest_result": "not_visible",
            "latest_observed_at": NOW, "note": "DO_NOT_EXPORT",
            "storage_key": "PRIVATE_PATH",
        }
        timeline = [
            {"event_type": "campaign_submitted", "created_at": NOW, "details_json": {"secret": True}},
            {"event_type": "placement_evidence_uploaded", "created_at": NOW},
            {"event_type": "payment_reviewed", "created_at": NOW},
        ]
        report = build_campaign_report(campaign(), observation, timeline, generated_at=NOW)
        self.assertEqual(report["tariff"]["price_kopecks"], 30000)
        self.assertEqual(report["verification"]["status"], "operator_reported_not_visible")
        self.assertFalse(report["verification"]["telegram_api_verified"])
        self.assertIsNone(report["verification"]["impressions_count"])
        self.assertEqual(len(report["timeline"]), 2)
        text = str(report)
        for secret in ("DO_NOT_EXPORT", "PRIVATE_PATH", "NEVER_EXPORT", "storage_key", "details_json"):
            self.assertNotIn(secret, text)

    def test_undefined_observations_do_not_create_delivery_claim(self):
        report = build_campaign_report(campaign(), {}, [], generated_at=NOW)
        self.assertEqual(report["operator_observations"]["total"], 0)
        self.assertEqual(report["verification"]["status"], "no_operator_report")
        self.assertFalse(report["operator_observations"]["private_screenshots_downloadable"])

    def test_owner_only_and_whitelisted_sql(self):
        self.login()
        conn = MagicMock()
        cursor = conn.__enter__.return_value.execute.return_value
        cursor.fetchone.side_effect = [
            campaign(),
            {"visible_count": 1, "not_visible_count": 0,
             "private_screenshot_count": 1, "latest_result": "visible",
             "latest_observed_at": NOW},
        ]
        cursor.fetchall.return_value = [{"event_type": "campaign_submitted", "created_at": NOW}]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operator_observations"]["visible"], 1)
        self.assertNotIn("owner_secret", response.text)
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("advertiser_user_id = %s", calls[0].args[0])
        self.assertEqual(calls[0].args[1], (CID, UID))
        self.assertIn("placement_evidence", calls[1].args[0])
        self.assertIn("event_type = ANY", calls[2].args[0])
        self.assertEqual(calls[2].args[1][0], CID)
        self.assertEqual(set(calls[2].args[1][1]), PUBLIC_EVENTS)
        self.assertTrue(all(c.args[0].strip().startswith("SELECT") for c in calls))

    def test_other_owner_404_does_not_query_observations(self):
        self.login()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_database_unavailable_503(self):
        import psycopg
        self.login()
        with patch("app.main.psycopg.connect", side_effect=psycopg.OperationalError("offline")):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 503)

    def test_ui_has_report_action_and_no_file_links(self):
        script = (ROOT / "api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("Отчёт по кампании", script)
        self.assertIn("/campaigns/'+c.id+'/report", script)
        self.assertIn("Количество показов Telegram API не подтверждено", script)

if __name__ == "__main__":
    unittest.main()
