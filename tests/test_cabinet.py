import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ["SESSION_SIGNING_KEY"] = "a" * 64
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import main


class CabinetTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.admin = {"id": uuid4(), "role": "admin"}

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_public_cabinet_and_assets(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})

    def test_public_config_has_no_secrets(self):
        self.assertEqual(set(self.client.get("/api/v1/public-config").json()), {"telegram_bot_username"})

    def test_anonymous_cannot_create_node(self):
        self.assertEqual(self.client.post("/api/v1/admin/proxy-nodes", json={"name": "Node", "host": "node.example.org", "port": 8443}).status_code, 401)

    def test_operator_cannot_manage_nodes(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "operator"}
        self.assertEqual(self.client.post("/api/v1/admin/proxy-nodes", json={"name": "Node", "host": "node.example.org", "port": 8443}).status_code, 403)

    def test_invalid_host_rejected_before_database(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        with patch.object(main.psycopg, "connect") as connect:
            response = self.client.post("/api/v1/admin/proxy-nodes", json={"name": "Node", "host": "https://node.example.org/path", "port": 8443})
            self.assertEqual(response.status_code, 422)
            connect.assert_not_called()

    def test_invalid_port(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        self.assertEqual(self.client.post("/api/v1/admin/proxy-nodes", json={"name": "Node", "host": "node.example.org", "port": 65536}).status_code, 422)

    def test_occupied_node_cannot_be_paused(self):
        main.app.dependency_overrides[main.require_admin] = lambda: self.admin
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [{"id": uuid4()}, {"occupied": 1}]
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.patch("/api/v1/admin/proxy-nodes/" + str(uuid4()) + "/status", json={"status": "paused"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(connection.execute.call_count, 2)

    def test_host_normalization(self):
        self.assertEqual(main.normalize_proxy_host("NODE.EXAMPLE.ORG"), "node.example.org")
        self.assertEqual(main.normalize_proxy_host("2001:db8::1"), "2001:db8::1")
        with self.assertRaises(HTTPException):
            main.normalize_proxy_host("bad..example.org")

    def test_tampered_session_is_rejected(self):
        token = main._create_session_token(uuid4(), uuid4())
        with self.assertRaises(HTTPException):
            main._decode_session_token(token.split(".")[0] + ".invalid")


    def test_foreign_campaign_correction_returns_404(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.patch("/api/v1/campaigns/" + str(uuid4()) + "/channel", json={"title": "Канал", "telegram_url": "https://t.me/example"})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(connection.execute.call_count, 1)

    def test_reviewed_channel_cannot_be_corrected(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [{"id": uuid4()}, {"blocked": 1}]
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.patch("/api/v1/campaigns/" + str(uuid4()) + "/channel", json={"title": "Канал", "telegram_url": "https://t.me/example"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(connection.execute.call_count, 2)

    def test_channel_correction_is_audited(self):
        user_id, channel_id, campaign_id = uuid4(), uuid4(), uuid4()
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": user_id, "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [
            {"id": channel_id}, None,
            {"id": channel_id, "title": "Исправлено", "telegram_url": "https://t.me/corrected"},
        ]
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.patch("/api/v1/campaigns/" + str(campaign_id) + "/channel", json={"title": "Исправлено", "telegram_url": "https://t.me/corrected"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["title"], "Исправлено")
            self.assertIn("campaign_channel_corrected", connection.execute.call_args.args[0])

    def test_resubmit_returned_campaign_preserves_tariff(self):
        user_id, campaign_id = uuid4(), uuid4()
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": user_id, "role": "advertiser"}
        row = dict(id=campaign_id, channel_id=uuid4(), tariff_id=uuid4(),
                   tariff_name_snapshot="1 день", duration_seconds_snapshot=86400,
                   price_kopecks_snapshot=30000, currency_snapshot="RUB",
                   moderation_status="pending", payment_status="not_required",
                   placement_status="not_scheduled", requested_start_at=None,
                   scheduled_start_at=None, scheduled_end_at=None, moderator_comment="Исправьте ссылку")
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [{"id": row["channel_id"]}, row]
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.post("/api/v1/campaigns/" + str(campaign_id) + "/submit")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["price_kopecks_snapshot"], 30000)
            sql = connection.execute.call_args_list[1].args[0]
            self.assertIn("'changes_requested'", sql)
            self.assertNotIn("SET price_kopecks_snapshot", sql)



    def test_foreign_payment_instructions_are_not_disclosed(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        with patch.object(main.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = self.client.get("/api/v1/campaigns/" + str(uuid4()) + "/payment-instructions")
            self.assertEqual(response.status_code, 404)
            query = connection.execute.call_args.args[0]
            self.assertIn("advertiser_user_id = %s", query)
            self.assertIn("moderation_status = 'approved'", query)

    def test_payment_instructions_use_snapshot_amount(self):
        import base64
        campaign_id = uuid4()
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            "id": campaign_id, "price_kopecks_snapshot": 30000, "currency_snapshot": "RUB"}
        with patch.object(main.psycopg, "connect") as connect, patch.dict(os.environ, {"PAYMENT_INSTRUCTIONS_B64": base64.b64encode("Получатель: Тест".encode()).decode()}):
            connect.return_value.__enter__.return_value = connection
            response = self.client.get("/api/v1/campaigns/" + str(campaign_id) + "/payment-instructions")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["amount_kopecks"], 30000)
            self.assertEqual(response.json()["reference"], str(campaign_id))
            self.assertEqual(response.json()["instructions"], "Получатель: Тест")
            self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_missing_payment_instructions_have_no_fake_details(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": uuid4(), "role": "advertiser"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"id": uuid4(), "price_kopecks_snapshot": 30000, "currency_snapshot": "RUB"}
        with patch.object(main.psycopg, "connect") as connect, patch.dict(os.environ, {"PAYMENT_INSTRUCTIONS_B64": ""}):
            connect.return_value.__enter__.return_value = connection
            response = self.client.get("/api/v1/campaigns/" + str(uuid4()) + "/payment-instructions")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"], "payment_instructions_not_configured")

    def test_anonymous_payment_instructions_denied(self):
        self.assertEqual(self.client.get("/api/v1/campaigns/" + str(uuid4()) + "/payment-instructions").status_code, 401)


if __name__ == "__main__":
    unittest.main()
