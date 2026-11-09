"""План сети: приоритет платных слотов, отказоустойчивость и доступ по ролям."""
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
from app.network_promotion import build_network_plan

WHEN = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
NODE = UUID("00000000-0000-4000-8000-000000000a01")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000a02")
ADMIN = UUID("00000000-0000-4000-8000-000000000a03")


def node(**overrides):
    item = dict(
        node_id=NODE, node_name="Pushok-1", node_status="active",
        target_engine="official_mtproxy", tag_registered=True,
        campaign_id=None, campaign_channel_url=None,
        moderation_status=None, payment_status=None, placement_status=None,
    )
    item.update(overrides)
    return item


class NetworkPlanTests(unittest.TestCase):
    def plan(self, rows):
        return build_network_plan(rows, at=WHEN, own_channel_url="https://t.me/pushok_proxy")

    def test_idle_node_proposes_own_channel_only(self):
        plan = self.plan([node()])
        self.assertEqual(plan["nodes"][0]["state"], "own_channel_planned")
        self.assertEqual(plan["nodes"][0]["channel_kind"], "own")
        self.assertEqual(plan["nodes"][0]["target_channel_url"], "https://t.me/pushok_proxy")
        self.assertFalse(plan["changes_applied"])
        self.assertFalse(plan["telegram_sponsor_verified"])
        self.assertFalse(plan["automatic_switch_supported"])

    def test_paid_slot_wins_over_own_channel(self):
        result = self.plan([node(
            campaign_id=CAMPAIGN, campaign_channel_url="https://t.me/AdvertiserOne",
            moderation_status="approved", payment_status="verified",
            placement_status="scheduled"
        )])["nodes"][0]
        self.assertEqual(result["state"], "paid_channel_planned")
        self.assertEqual(result["target_channel_url"], "https://t.me/AdvertiserOne")
        self.assertEqual(result["operator_action"], "manually_verify_paid_sponsor")
        self.assertFalse(result["display_verified"])

    def test_unverified_payment_does_not_fall_back_to_own(self):
        result = self.plan([node(
            campaign_id=CAMPAIGN, campaign_channel_url="https://t.me/AdvertiserOne",
            moderation_status="approved", payment_status="proof_submitted",
            placement_status="scheduled"
        )])["nodes"][0]
        self.assertEqual(result["state"], "paid_placement_not_approved")
        self.assertNotEqual(result["channel_kind"], "own")

    def test_paused_and_unregistered_nodes_not_promised(self):
        self.assertEqual(self.plan([node(node_status="paused")])["nodes"][0]["state"], "node_paused")
        self.assertEqual(self.plan([node(tag_registered=False)])["nodes"][0]["state"], "tag_registration_required")

    def test_conflicting_campaigns_fail_closed(self):
        result = self.plan([
            node(campaign_id=CAMPAIGN, campaign_channel_url="https://t.me/AdvertiserOne"),
            node(campaign_id=UUID("00000000-0000-4000-8000-000000000a04"),
                 campaign_channel_url="https://t.me/AdvertiserTwo"),
        ])["nodes"][0]
        self.assertEqual(result["state"], "overlap_conflict")
        self.assertIsNone(result["target_channel_url"])

    def test_reject_invalid_timezone_and_own_channel(self):
        with self.assertRaises(ValueError):
            build_network_plan([node()], at=datetime(2026, 11, 15), own_channel_url="https://t.me/pushok_proxy")
        with self.assertRaises(ValueError):
            build_network_plan([node()], at=WHEN, own_channel_url="https://evil.t.me/Channel")

    def test_no_secrets_or_claimed_impressions(self):
        result = str(self.plan([node(target_engine="telemt")]))
        self.assertNotIn("ad_tag", result)
        self.assertNotIn("proxy_secret", result)
        self.assertNotIn("impressions", result)


class NetworkApiTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.path = "/api/v1/admin/network/promotion-plan"

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def test_anonymous_and_operator_blocked_before_database(self):
        with patch("app.network_promotion.psycopg.connect") as db:
            self.assertEqual(self.client.get(self.path).status_code, 401)
            main.app.dependency_overrides[main.get_current_user] = lambda: {"id": ADMIN, "role": "operator"}
            self.assertEqual(self.client.get(self.path).status_code, 403)
            db.assert_not_called()

    def test_admin_receives_read_only_plan(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": ADMIN, "role": "admin"}
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchall.return_value = [node()]
        with patch("app.network_promotion.psycopg.connect", return_value=connection):
            response = self.client.get(self.path, params={"at": WHEN.isoformat()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["summary"]["own_channel_planned"], 1)
        sql, params = connection.__enter__.return_value.execute.call_args.args
        self.assertTrue(sql.strip().startswith("SELECT"))
        self.assertEqual(params, (WHEN, WHEN))
        self.assertNotIn("r.ad_tag", sql)
        self.assertNotIn("INSERT", sql)

    def test_reject_naive_time_without_db(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": ADMIN, "role": "admin"}
        with patch("app.network_promotion.psycopg.connect") as db:
            response = self.client.get(self.path + "?at=2026-11-15T12:00:00")
        self.assertEqual(response.status_code, 422)
        db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
