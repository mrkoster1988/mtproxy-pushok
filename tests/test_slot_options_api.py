"""v0.036: права доступа и read-only подбор слота."""
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

CAMPAIGN=UUID("00000000-0000-4000-8000-000000000960")
USER=UUID("00000000-0000-4000-8000-000000000961")
NODE=UUID("00000000-0000-4000-8000-000000000962")

class SlotOptionsAPITests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client=TestClient(main.app)
        self.start=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def url(self):
        return f"/api/v1/campaigns/{CAMPAIGN}/slot-options"

    def login(self):
        main.app.dependency_overrides[main.get_current_user]=lambda: {"id":USER,"role":"advertiser"}

    def test_authentication_required_without_database_access(self):
        with patch("app.main.psycopg.connect") as db:
            result=self.client.get(self.url(),params={"start_at":self.start})
            self.assertEqual(result.status_code,401)
            db.assert_not_called()

    def test_naive_and_out_of_range_rejected_before_db(self):
        self.login()
        for start in ("2026-11-10T12:00:00", "2000-01-01T12:00:00+00:00"):
            with self.subTest(start=start),patch("app.main.psycopg.connect") as db:
                result=self.client.get(self.url(),params={"start_at":start})
                self.assertEqual(result.status_code,422)
                db.assert_not_called()

    def test_only_owners_approved_unpaid_campaign_eligible(self):
        self.login()
        conn=MagicMock()
        cursor=conn.__enter__.return_value.execute.return_value
        cursor.fetchone.return_value={
            "duration_seconds_snapshot":86400,"moderation_status":"approved",
            "payment_status":"awaiting_payment","placement_status":"not_scheduled"}
        cursor.fetchall.return_value=[{"id":NODE}]
        with patch("app.main.psycopg.connect",return_value=conn):
            result=self.client.get(self.url(),params={"start_at":self.start})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["nodes"],[{"id":str(NODE),"title":"Прокси №1"}])
        self.assertNotIn("host",result.text)
        self.assertFalse(result.json()["reservation_created"])
        calls=conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0].args[1],(CAMPAIGN,USER))
        self.assertIn("h.expires_at > now()",calls[1].args[0])
        self.assertIn("c.placement_status IN ('scheduled', 'active')",calls[1].args[0])
        self.assertTrue(all(x.args[0].strip().startswith("SELECT") for x in calls))

    def test_other_owner_is_not_found(self):
        self.login()
        conn=MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value=None
        with patch("app.main.psycopg.connect",return_value=conn):
            result=self.client.get(self.url(),params={"start_at":self.start})
        self.assertEqual(result.status_code,404)
        self.assertEqual(result.json()["detail"],"campaign_not_found")

    def test_unapproved_campaign_does_not_query_nodes(self):
        self.login()
        conn=MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value={
            "duration_seconds_snapshot":86400,"moderation_status":"pending",
            "payment_status":"awaiting_payment","placement_status":"not_scheduled"}
        with patch("app.main.psycopg.connect",return_value=conn):
            result=self.client.get(self.url(),params={"start_at":self.start})
        self.assertEqual(result.status_code,409)
        self.assertEqual(conn.__enter__.return_value.execute.call_count,1)

    def test_frontend_uses_existing_hold_api(self):
        source=(ROOT/"api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("slot-options?",source)
        self.assertIn("Бронь на 15 минут",source)
        self.assertIn("remaining_seconds",source)
        self.assertIn("method:'DELETE'",source)

if __name__=="__main__":
    unittest.main()
