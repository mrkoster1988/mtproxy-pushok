"""Совместный регрессионный контракт PR #19 + #20: план read-only, активация fail-closed."""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)

from app import main

NODE = UUID("00000000-0000-4000-8000-000000000a01")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000a02")
ADMIN = UUID("00000000-0000-4000-8000-000000000a03")
WHEN = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
ATTESTATION = {
    "tag_applied_on_node": True,
    "mtproxybot_promotion_selected": True,
    "independent_client_checked": True,
    "verification_note": "Отдельный неподписанный Telegram-клиент проверен оператором.",
}


class SponsorNetworkIntegrationTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def test_routes_share_one_app_and_enforce_roles(self):
        self.assertEqual(len([r for r in main.app.routes
            if getattr(r, "path", "") == "/api/v1/admin/network/promotion-plan"]), 1)
        self.assertEqual(len([r for r in main.app.routes
            if getattr(r, "path", "") == "/api/v1/operator/campaigns/{campaign_id}/activate"]), 1)
        with patch("app.network_promotion.psycopg.connect") as plan_db, \
             patch("app.main.psycopg.connect") as activation_db:
            self.assertEqual(self.client.get("/api/v1/admin/network/promotion-plan").status_code, 401)
            self.assertEqual(self.client.post(
                f"/api/v1/operator/campaigns/{CAMPAIGN}/activate",
                json=ATTESTATION).status_code, 401)
            plan_db.assert_not_called()
            activation_db.assert_not_called()

    def test_paid_plan_never_triggers_activation(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": ADMIN, "role": "admin"}
        rows = [{
            "node_id": NODE, "node_name": "Pushok-1", "node_status": "active",
            "target_engine": "official_mtproxy", "tag_registered": True,
            "registration_engine": "official_mtproxy",
            "campaign_id": CAMPAIGN, "campaign_channel_url": "https://t.me/AdvertiserOne",
            "telegram_channel_id": -100123456789,
            "moderation_status": "approved", "payment_status": "verified",
            "placement_status": "scheduled",
        }]
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchall.return_value = rows
        # Both modules import the same psycopg module: a second connect patch
        # would shadow the test database and return an empty mock plan.
        with patch("app.network_promotion.psycopg.connect", return_value=db):
            response = self.client.get("/api/v1/admin/network/promotion-plan",
                                       params={"at": WHEN.isoformat()})
            self.assertEqual(response.status_code, 200, response.text)
            node = response.json()["nodes"][0]
            self.assertEqual(node["state"], "paid_channel_planned")
            self.assertEqual(node["target_channel_url"], "https://t.me/AdvertiserOne")
            self.assertFalse(node["display_verified"])
            self.assertFalse(node["changes_applied"])
            self.assertFalse(node["automatic_switch_supported"])
            query = db.__enter__.return_value.execute.call_args.args[0]
            self.assertTrue(query.lstrip().startswith("SELECT"))
            self.assertNotIn("UPDATE campaigns", query)

    def test_plan_does_not_bypass_observation_gate(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {
            "id": ADMIN, "role": "operator"}
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.side_effect = [
            None, {"id": CAMPAIGN}]
        with patch("app.main.psycopg.connect", return_value=db):
            response = self.client.post(
                f"/api/v1/operator/campaigns/{CAMPAIGN}/activate", json=ATTESTATION)
        self.assertEqual(response.status_code, 409, response.text)
        sql = db.__enter__.return_value.execute.call_args_list[0].args[0]
        self.assertIn("placement_observations", sql)
        self.assertIn("newest.id DESC", sql)
        self.assertIn("o.result = 'visible'", sql)
        self.assertIn("interval '10 minutes'", sql)
        self.assertIn("c.payment_status = 'verified'", sql)
        self.assertIn("proxy_sponsor_registrations", sql)


if __name__ == "__main__":
    unittest.main()
