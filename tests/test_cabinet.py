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


if __name__ == "__main__":
    unittest.main()
