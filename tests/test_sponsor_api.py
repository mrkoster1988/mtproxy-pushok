"""API спонсора: RBAC, отсутствие применения настроек и SQL-параметры."""
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

NODE = UUID("00000000-0000-4000-8000-000000000702")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000701")
ADMIN = UUID("00000000-0000-4000-8000-000000000703")
TAG = "1234567890abcdef1234567890abcdef"


def fake_connection(*items):
    conn = MagicMock()
    conn.__enter__.return_value.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row}) for row in items
    ]
    return conn


def prepared_row():
    return {
        "campaign_id": CAMPAIGN, "proxy_node_id": NODE,
        "proxy_host": "mtproxy.pushok.shop", "proxy_port": 443,
        "node_status": "active", "engine": "telemt", "ad_tag": TAG,
        "moderation_status": "approved", "payment_status": "verified",
        "placement_status": "scheduled", "telegram_channel_id": -10012345,
        "telegram_url": "https://t.me/ExampleChannel",
        "scheduled_start_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "scheduled_end_at": datetime.now(timezone.utc) + timedelta(days=1),
    }


class SponsorEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.admin = {"id": ADMIN, "role": "admin"}
        self.operator = {"id": ADMIN, "role": "operator"}
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def register(self, tag=TAG, engine="telemt"):
        return self.client.put(
            f"/api/v1/admin/proxy-nodes/{NODE}/sponsor-registration",
            json={"ad_tag": tag, "engine": engine},
        )

    def test_unauthenticated_registration_is_blocked(self):
        with patch("app.main.psycopg.connect") as db:
            result = self.register()
        self.assertIn(result.status_code, (401, 403))
        db.assert_not_called()

    def test_non_admin_role_is_rejected_before_sql(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": ADMIN, "role": "operator",
        }
        with patch("app.main.psycopg.connect") as db:
            result = self.register()
        self.assertEqual(result.status_code, 403)
        db.assert_not_called()

    def test_invalid_tag_cannot_reach_database(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        for bad in ("0" * 32, "x" * 32, "f" * 31):
            with self.subTest(bad=bad), patch("app.main.psycopg.connect") as db:
                result = self.register(tag=bad)
                self.assertEqual(result.status_code, 422)
                db.assert_not_called()

    def test_upsert_masks_tag_and_writes_audit_without_tag(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        db = fake_connection({"id": NODE}, None, None, None)
        with patch("app.main.psycopg.connect", return_value=db):
            result = self.register(engine="telemt")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["tag_ending"], TAG[-4:])
        self.assertFalse(result.json()["changes_applied"])
        self.assertNotIn(TAG, result.text)
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertIn("ON CONFLICT (proxy_node_id)", calls[2].args[0])
        self.assertEqual(calls[2].args[1], (NODE, "telemt", TAG, ADMIN))
        self.assertIn("sponsor_tag_registered", calls[3].args[0])
        self.assertNotIn(TAG, str(calls[3].args))

    def test_cannot_change_tag_when_node_has_placement(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        db = fake_connection({"id": NODE}, {"1": True})
        with patch("app.main.psycopg.connect", return_value=db):
            result = self.register()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 2)
        self.assertIsNotNone(db.__exit__.call_args.args[0])

    def test_config_preview_is_admin_only_and_read_only(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        db = fake_connection({"engine": "telemt", "ad_tag": TAG})
        with patch("app.main.psycopg.connect", return_value=db):
            result = self.client.get(
                f"/api/v1/admin/proxy-nodes/{NODE}/sponsor-config-preview"
            )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn(f'ad_tag = "{TAG}"', result.json()["config_fragment"])
        self.assertFalse(result.json()["changes_applied"])
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)

    def test_official_docker_engine_accepts_tag_and_renders_env(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        db = fake_connection({"id": NODE}, None, None, None)
        with patch("app.main.psycopg.connect", return_value=db):
            registered = self.register(engine="official_docker")
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["engine"], "official_docker")
        preview_db = fake_connection({"engine": "official_docker", "ad_tag": TAG})
        with patch("app.main.psycopg.connect", return_value=preview_db):
            preview = self.client.get(
                f"/api/v1/admin/proxy-nodes/{NODE}/sponsor-config-preview"
            )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["config_fragment"], "TAG=" + TAG)

    def test_operator_preview_redacts_tag_and_never_writes(self):
        main.app.dependency_overrides[main.require_operator] = lambda: self.operator
        db = fake_connection(prepared_row())
        with patch("app.main.psycopg.connect", return_value=db):
            result = self.client.get(
                f"/api/v1/operator/campaigns/{CAMPAIGN}/sponsor-readiness"
            )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["ready_for_manual_preparation"])
        self.assertFalse(result.json()["changes_applied"])
        self.assertNotIn(TAG, result.text)
        self.assertEqual(result.json()["tag_ending"], TAG[-4:])
        calls = db.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], (CAMPAIGN,))

    def test_campaign_not_found(self):
        main.app.dependency_overrides[main.require_operator] = lambda: self.operator
        with patch("app.main.psycopg.connect",
                   return_value=fake_connection(None)):
            result = self.client.get(
                f"/api/v1/operator/campaigns/{CAMPAIGN}/sponsor-readiness"
            )
        self.assertEqual(result.status_code, 404)


if __name__ == "__main__":
    unittest.main()
