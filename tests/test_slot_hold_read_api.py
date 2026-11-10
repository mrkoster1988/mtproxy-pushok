"""v0.035 — запрос статуса брони не раскрывает чужие кампании."""
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

USER = UUID("00000000-0000-4000-8000-000000000901")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000902")
NODE = UUID("00000000-0000-4000-8000-000000000903")
HOLD = UUID("00000000-0000-4000-8000-000000000904")


class HoldReadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def request(self):
        return self.client.get(f"/api/v1/campaigns/{CAMPAIGN}/slot-hold")

    def test_login_required_before_database(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.request()
            self.assertEqual(response.status_code, 401)
            db.assert_not_called()

    def test_owner_only_and_read_only(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": USER, "role": "advertiser"}
        now = datetime.now(timezone.utc)
        row = {"id": HOLD, "campaign_id": CAMPAIGN, "proxy_node_id": NODE,
               "starts_at": now + timedelta(days=1), "ends_at": now + timedelta(days=2),
               "expires_at": now + timedelta(minutes=10), "remaining_seconds": 600}
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = row
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["remaining_seconds"], 600)
        self.assertFalse(response.json()["payment_verified"])
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("c.advertiser_user_id = %s", calls[0].args[0])
        self.assertIn("h.expires_at > now()", calls[0].args[0])
        self.assertEqual(calls[0].args[1], (CAMPAIGN, USER))
        self.assertTrue(calls[0].args[0].lstrip().startswith("SELECT"))

    def test_absent_or_expired_or_other_owner_returns_404(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": USER, "role": "advertiser"}
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.request()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "active_slot_hold_not_found")


if __name__ == "__main__":
    unittest.main()
