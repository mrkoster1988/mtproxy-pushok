"""v0.050 — конфигурация платежей, только admin, без ключей и неподтверждённых обещаний."""
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

ADMIN = UUID("00000000-0000-4000-8000-000000000997")
PATH = "/api/v1/admin/payments/status"


class AdminPaymentSettingsTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()

    def test_anonymous_cannot_access_without_database(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_operator_cannot_read_settings(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": ADMIN, "role": "operator"
        }
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get(PATH)
        self.assertEqual(response.status_code, 403)
        db.assert_not_called()

    def test_admin_sees_safe_counts_without_secrets(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": ADMIN, "role": "admin"
        }
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "priced_stars_tariffs": 2, "disputed_stars_receipts": 1,
            "recorded_stars_payments": 3, "recorded_sbp_payments": 0,
        }
        with patch.dict(os.environ, {
            "PUSHOK_STARS_PAYMENTS_ENABLED": "0",
            "TELEGRAM_BOT_TOKEN": "SECRET_TEST_TOKEN",
        }):
            with patch("app.main.psycopg.connect", return_value=conn):
                response = self.client.get(PATH)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["stars"]["priced_active_tariffs"], 2)
        self.assertEqual(data["stars"]["needs_manual_review"], 1)
        self.assertFalse(data["stars"]["invoice_enabled_in_server_config"])
        self.assertFalse(data["stars"]["telegram_acceptance_verified"])
        self.assertEqual(data["sbp"]["integration"], "external_web_checkout")
        self.assertFalse(data["sbp"]["checkout_enabled"])
        self.assertFalse(data["secrets_exposed"])
        self.assertNotIn("SECRET_TEST_TOKEN", response.text)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_server_flag_reported_without_claiming_acceptance(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": ADMIN, "role": "admin"
        }
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {}
        with patch.dict(os.environ, {"PUSHOK_STARS_PAYMENTS_ENABLED": "1"}):
            with patch("app.main.psycopg.connect", return_value=conn):
                response = self.client.get(PATH)
        self.assertTrue(response.json()["stars"]["invoice_enabled_in_server_config"])
        self.assertFalse(response.json()["stars"]["telegram_acceptance_verified"])
        self.assertFalse(response.json()["operator_can_enable_payments_from_ui"])


if __name__ == "__main__":
    unittest.main()
