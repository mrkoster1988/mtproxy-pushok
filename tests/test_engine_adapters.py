"""Pushok Unified Engine: чистая политика, API/RBAC и отсутствие реального запуска."""
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
from app.engine_adapters import get_adapter, engine_catalog, recommend_engine

ADMIN = UUID("00000000-0000-4000-8000-000000000825")
NODE = UUID("00000000-0000-4000-8000-000000000826")


class UnifiedEngineTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def as_admin(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": ADMIN, "role": "admin"
        }

    def test_catalog_three_adapters_and_distinct_launch_modes(self):
        catalog = engine_catalog()
        self.assertEqual({x["id"] for x in catalog},
                         {"official_mtproxy", "official_docker", "telemt"})
        self.assertEqual(get_adapter("official_docker").deployment, "docker")
        self.assertFalse(get_adapter("telemt").automatic_eligible)
        self.assertFalse(get_adapter("official_docker").production_eligible)
        with self.assertRaises(ValueError):
            get_adapter("unknown")

    def test_auto_defaults_to_classic_without_applying(self):
        plan = recommend_engine()
        self.assertEqual(plan["engine"], "official_mtproxy")
        self.assertFalse(plan["changes_applied"])
        self.assertFalse(plan["provisioning_verified"])

    def test_docker_opt_in_is_staging_only(self):
        plan = recommend_engine(deployment="docker", environment="staging")
        self.assertEqual(plan["engine"], "official_docker")
        self.assertEqual(plan["availability"], "experimental_image")
        with self.assertRaisesRegex(ValueError, "no_verified_engine_for_profile"):
            recommend_engine(deployment="docker", environment="production")

    def test_manual_telemt_is_plan_only(self):
        plan = recommend_engine(engine_mode="manual", preferred_engine="telemt",
                                environment="staging", deployment="native")
        self.assertEqual(plan["availability"], "adapter_only")
        self.assertFalse(plan["provisioning_verified"])
        with self.assertRaisesRegex(ValueError, "engine_not_approved_for_production"):
            recommend_engine(engine_mode="manual", preferred_engine="telemt")

    def test_invalid_preferences_are_rejected(self):
        for data in (
            {"engine_mode": "manual"},
            {"engine_mode": "auto", "preferred_engine": "telemt"},
            {"engine_mode": "manual", "preferred_engine": "telemt",
             "deployment": "docker", "environment": "staging"},
            {"deployment": "invalid"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                recommend_engine(**data)

    def test_admin_api_read_only_and_role_gated(self):
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.client.get("/api/v1/admin/proxy-engines").status_code, 401)
            self.assertEqual(self.client.post("/api/v1/admin/proxy-engines/recommend",
                                             json={}).status_code, 401)
            db.assert_not_called()
        self.as_admin()
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get("/api/v1/admin/proxy-engines")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["adapters"]), 3)
            recommendation = self.client.post(
                "/api/v1/admin/proxy-engines/recommend",
                json={"deployment": "docker", "environment": "staging"})
            self.assertEqual(recommendation.status_code, 200)
            self.assertEqual(recommendation.json()["engine"], "official_docker")
            db.assert_not_called()

    def test_docker_production_refused_before_sql(self):
        self.as_admin()
        with patch("app.main.psycopg.connect") as db:
            response = self.client.post("/api/v1/admin/proxy-engines/recommend",
                                        json={"deployment": "docker", "environment": "production"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"], "no_verified_engine_for_profile")
            db.assert_not_called()

    def test_creation_saves_target_not_runtime(self):
        self.as_admin()
        row = {"id": NODE, "name": "test", "host": "node.example.org",
               "port": 8443, "status": "active", "target_engine": "official_mtproxy",
               "engine_selection_mode": "auto", "deployment_profile": "native"}
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row
        with patch("app.main.psycopg.connect") as db:
            db.return_value.__enter__.return_value = conn
            result = self.client.post("/api/v1/admin/proxy-nodes",
                                      json={"name": "test", "host": "node.example.org", "port": 8443})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["target_engine"], "official_mtproxy")
        self.assertFalse(result.json()["provisioning_verified"])
        self.assertIn("target_engine", conn.execute.call_args_list[0].args[0])
        self.assertEqual(conn.execute.call_args_list[0].args[1],
                         ("test", "node.example.org", 8443, "official_mtproxy", "auto", "native"))

    def test_conflicting_sponsor_engine_blocked(self):
        self.as_admin()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "id": NODE, "target_engine": "official_mtproxy"}
        with patch("app.main.psycopg.connect") as db:
            db.return_value.__enter__.return_value = conn
            result = self.client.put(
                f"/api/v1/admin/proxy-nodes/{NODE}/sponsor-registration",
                json={"engine": "telemt", "ad_tag": "a" * 32})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"], "sponsor_engine_mismatch")
        self.assertEqual(conn.execute.call_count, 1)

    def test_legacy_nodes_remain_unassigned(self):
        migration = (ROOT / "database/009_unified_engine_selection.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS target_engine", migration)
        self.assertNotIn("UPDATE proxy_nodes SET", migration)
        self.assertIn("009_unified_engine_selection.sql", (ROOT / "setup.sh").read_text())


if __name__ == "__main__":
    unittest.main()
