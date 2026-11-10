"""v0.045 — честная сводка до запуска, RBAC и отсутствие секретов."""
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
from app.launch_readiness import build_launch_readiness

ADMIN = UUID("00000000-0000-4000-8000-000000000951")


class LaunchReadinessTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.path = "/api/v1/admin/launch-readiness"

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def test_unauthenticated_401_before_db(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_operator_not_admin_403(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": ADMIN, "role": "operator"}
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 403)
        db.assert_not_called()

    def test_empty_database_never_ready(self):
        report = build_launch_readiness({})
        self.assertEqual(report["statistics"]["nodes_total"], 0)
        self.assertFalse(report["ready_for_public_launch"])
        self.assertFalse(report["real_sponsor_verified"])
        self.assertFalse(report["ci_verified"])
        self.assertEqual(report["scope"], "read_only_database_inventory")
        self.assertEqual(next(x for x in report["checks"] if x["id"] == "nodes_in_database")["status"], "missing")

    def test_populated_database_is_not_telegram_evidence(self):
        report = build_launch_readiness({
            "nodes_total": 3, "nodes_active": 2,
            "nodes_with_registered_tag": 2, "campaigns_total": 21,
            "payments_verified": 12, "placements_active": 2,
            "manual_observations": 4, "ad_tag": "SECRET_NOT_EXPORT",
        })
        self.assertEqual(report["statistics"]["nodes_total"], 3)
        self.assertFalse(report["ready_for_public_launch"])
        self.assertFalse(report["real_telegram_verified"])
        self.assertFalse(report["real_sponsor_verified"])
        self.assertFalse(report["payments_automatically_verified"])
        self.assertFalse(report["changes_applied"])
        self.assertNotIn("SECRET_NOT_EXPORT", str(report))
        self.assertEqual(next(x for x in report["checks"] if x["id"] == "real_sponsor")["status"], "not_verified")

    def test_admin_endpoint_only_selects_aggregates(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": ADMIN, "role": "admin"}
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "nodes_total": 2, "nodes_active": 1, "nodes_with_registered_tag": 1,
            "campaigns_total": 5, "moderation_pending": 1, "payments_pending": 1,
            "payments_verified": 1, "placements_scheduled": 1,
            "placements_active": 0, "placements_completed": 0,
            "manual_observations": 0,
        }
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["statistics"]["nodes_active"], 1)
        self.assertFalse(payload["ready_for_public_launch"])
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 1)
        query = calls[0].args[0].strip()
        self.assertTrue(query.startswith("SELECT"))
        self.assertNotIn("ad_tag", query)
        self.assertNotIn("SELECT *", query)
        self.assertNotIn("INSERT", query)
        self.assertNotIn("UPDATE", query)
        self.assertNotIn("DELETE", query)

    def test_database_unavailable_503(self):
        import psycopg
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": ADMIN, "role": "admin"}
        with patch("app.main.psycopg.connect", side_effect=psycopg.OperationalError("offline")):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 503)

    def test_cabinet_wires_readiness_and_renders_safely(self):
        html = (ROOT / "api/web/index.html").read_text(encoding="utf-8")
        js = (ROOT / "api/web/app.js").read_text(encoding="utf-8")
        self.assertIn('id="launch-readiness"', html)
        self.assertIn("/admin/launch-readiness", js)
        self.assertIn("Готовность к публичному запуску: НЕ ПОДТВЕРЖДЕНА", js)
        self.assertNotIn("innerHTML", js)


if __name__ == "__main__":
    unittest.main()
