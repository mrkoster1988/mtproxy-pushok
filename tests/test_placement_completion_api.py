"""v0.046: завершение требует ручного подтверждения снятия спонсора."""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

CID = UUID("00000000-0000-4000-8000-000000000981")
UID = UUID("00000000-0000-4000-8000-000000000982")
TARGET = "/api/v1/operator/campaigns/" + str(CID) + "/complete"
VALID = {"tag_removed_confirmed": True, "own_channel_restored_confirmed": True,
         "verification_method": "separate_unsubscribed_client"}


def campaign_row():
    now = datetime.now(timezone.utc)
    return {
        "id": CID,
        "channel_id": UID,
        "tariff_id": UUID("00000000-0000-4000-8000-000000000001"),
        "tariff_name_snapshot": "1 день",
        "duration_seconds_snapshot": 86400,
        "price_kopecks_snapshot": 30000,
        "currency_snapshot": "RUB",
        "moderation_status": "approved",
        "payment_status": "verified",
        "placement_status": "completed",
        "requested_start_at": now,
        "scheduled_start_at": now - timedelta(hours=2),
        "scheduled_end_at": now + timedelta(days=1),
        "moderator_comment": None,
    }


class PlacementCompletionTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def as_operator(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {"id": UID, "role": "operator"}

    def test_anonymous_rejected_before_database(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.client.post(TARGET, json=VALID)
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_missing_confirmation_rejected_without_writing(self):
        self.as_operator()
        with patch("app.main.psycopg.connect") as db:
            for payload in ({}, {"tag_removed_confirmed": False,
                                 "own_channel_restored_confirmed": True,
                                 "verification_method": "separate_unsubscribed_client"},
                            {"tag_removed_confirmed": True,
                             "verification_method": "separate_unsubscribed_client"},
                            {"tag_removed_confirmed": True,
                             "own_channel_restored_confirmed": False,
                             "verification_method": "separate_unsubscribed_client"},
                            {"tag_removed_confirmed": True,
                             "own_channel_restored_confirmed": True,
                             "verification_method": "unknown_method"}):
                with self.subTest(payload=payload):
                    response = self.client.post(TARGET, json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
            db.assert_not_called()

    def test_success_writes_audit_not_tag_secret(self):
        self.as_operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = campaign_row()
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(TARGET, json=VALID)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["placement_status"], "completed")
        commands = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(commands), 2)
        self.assertIn("placement_status = 'active'", commands[0].args[0])
        self.assertIn("placement_completed_manually", commands[1].args[0])
        self.assertIn("tag_removed_confirmed", commands[1].args[0])
        self.assertIn("own_channel_restored_confirmed", commands[1].args[0])
        self.assertIn("operator_attestation", commands[1].args[0])
        self.assertIn("'telegram_api_verified', false", commands[1].args[0])
        self.assertEqual(commands[1].args[1], (UID, CID, "separate_unsubscribed_client"))
        self.assertNotIn("ad_tag", str(commands))

    def test_not_active_returns_409_without_audit(self):
        self.as_operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [
            None, {"id": CID}
        ]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(TARGET, json=VALID)
        self.assertEqual(response.status_code, 409, response.text)
        commands = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(commands), 2)
        self.assertNotIn("audit_events", str(commands))

    def test_not_found_returns_404(self):
        self.as_operator()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [None, None]
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.post(TARGET, json=VALID)
        self.assertEqual(response.status_code, 404, response.text)

    def test_cabinet_requires_manual_confirmation(self):
        js = (ROOT / "api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("tag_removed_confirmed:true", js)
        self.assertIn("separate_unsubscribed_client", js)
        self.assertIn("own_channel_restored_confirmed:true", js)
        self.assertIn("восстановили СОБСТВЕННЫЙ канал Pushok", js)


if __name__ == "__main__":
    unittest.main()
