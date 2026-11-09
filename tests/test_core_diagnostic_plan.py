"""v0.031: distinguish malformed protocol input checks from a real MTProxy handshake."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)

from app import main
from app.core_readiness import core_diagnostic_plan, core_readiness


class CoreDiagnosticPlanTests(unittest.TestCase):
    def test_plan_does_not_claim_valid_handshake_or_telegram(self):
        plan = core_diagnostic_plan()
        self.assertEqual(plan["project_version"], "0.031")
        self.assertEqual(plan["execution"], "not_started_by_api")
        self.assertEqual(plan["ci_probe_scope"], "offline_127.0.0.1_in_docker_network_none")
        self.assertFalse(plan["current_host_checked"])
        self.assertFalse(plan["real_telegram_verified"])
        self.assertFalse(plan["sponsor_verified"])
        self.assertFalse(plan["live_service_changed"])
        by_id = {x["id"]: x for x in plan["steps"]}
        self.assertEqual(len(by_id), len(plan["steps"]))
        for name in ("binary", "tcp_accept", "invalid_header", "reconnect", "stop"):
            self.assertEqual(by_id[name]["verification"], "ci_only")
        for name in ("valid_handshake", "telegram_client", "sponsor", "service_rollback"):
            self.assertEqual(by_id[name]["verification"], "not_verified")
        self.assertEqual(core_readiness()["runtime_status"], "not_checked")
        self.assertFalse(core_readiness()["changes_applied"])

    def test_admin_route_requires_auth_and_does_not_access_host(self):
        client = TestClient(main.app)
        main.app.dependency_overrides.clear()
        try:
            response = client.get("/api/v1/admin/pushok-core/diagnostic-plan")
            self.assertEqual(response.status_code, 401)
            main.app.dependency_overrides[main.require_admin] = lambda: {
                "id": "00000000-0000-4000-8000-000000000029", "role": "admin",
            }
            with patch("app.main.psycopg.connect") as database:
                response = client.get("/api/v1/admin/pushok-core/diagnostic-plan")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), core_diagnostic_plan())
                database.assert_not_called()
        finally:
            main.app.dependency_overrides.clear()
            client.close()

    def test_cabinet_explains_ci_only_vs_pending_checks(self):
        html = (ROOT / "api/web/index.html").read_text(encoding="utf-8")
        js = (ROOT / "api/web/app.js").read_text(encoding="utf-8")
        self.assertIn('id="core-test-plan"', html)
        self.assertIn("admin/pushok-core/diagnostic-plan", js)
        self.assertIn("Ещё не подтверждено", js)
        self.assertIn("Проверка в CI", js)
        self.assertIn("replaceChildren", js)
        self.assertNotIn("innerHTML", js)


if __name__ == "__main__":
    unittest.main()
