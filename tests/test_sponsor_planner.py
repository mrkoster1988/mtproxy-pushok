"""Юнит-проверки безопасного планирования спонсора MTProxy."""
import sys
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.sponsor_planner import (
    build_readiness, normalize_ad_tag, parse_public_channel, render_config_fragment,
)

TAG = "1234567890abcdef1234567890abcdef"
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000701")
NODE = UUID("00000000-0000-4000-8000-000000000702")


def scheduled_row():
    return {
        "campaign_id": CAMPAIGN, "proxy_node_id": NODE,
        "moderation_status": "approved", "payment_status": "verified",
        "placement_status": "scheduled",
        "node_status": "active", "proxy_host": "mtproxy.pushok.shop",
        "proxy_port": 443, "telegram_url": "https://t.me/ExampleChannel",
        "telegram_channel_id": -1001234567,
        "ad_tag": TAG, "engine": "telemt",
        "scheduled_start_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "scheduled_end_at": datetime.now(timezone.utc) + timedelta(days=1),
    }


class SponsorPlannerTests(unittest.TestCase):
    def test_valid_tag_normalizes_to_lowercase(self):
        self.assertEqual(normalize_ad_tag(TAG.upper()), TAG)
        self.assertEqual(normalize_ad_tag("  " + TAG + "  "), TAG)

    def test_placeholders_and_invalid_tags_rejected(self):
        for tag in ("", "0" * 32, "a" * 31, "x" * 32, "$ENV_TOKEN",
                    "abcd; rm -rf /", "f" * 33, None):
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError):
                    normalize_ad_tag(tag)

    def test_telemt_snippet_is_valid_toml_and_not_executed(self):
        fragment = render_config_fragment("telemt", TAG)
        settings = tomllib.loads(fragment)
        self.assertEqual(settings["general"]["ad_tag"], TAG)
        self.assertTrue(settings["general"]["use_middle_proxy"])
        self.assertNotIn("systemctl", fragment)
        self.assertNotIn("sudo", fragment)

    def test_official_mtproxy_has_only_tag_argument(self):
        fragment = render_config_fragment("official_mtproxy", TAG)
        self.assertEqual(fragment, "-P " + TAG)
        with self.assertRaises(ValueError):
            render_config_fragment("unknown_engine", TAG)

    def test_official_docker_tag_is_env_value_not_native_flag(self):
        fragment = render_config_fragment("official_docker", TAG)
        self.assertEqual(fragment, "TAG=" + TAG)
        self.assertNotIn("-P ", fragment)

    def test_public_channel_link_validation(self):
        self.assertEqual(parse_public_channel("https://t.me/ExampleChannel"),
                         "ExampleChannel")
        for value in ("https://t.me/+invitation", "http://t.me/ExampleChannel",
                      "https://t.me/joinchat", "https://t.me:443/ExampleChannel",
                      "https://evil.t.me/ExampleChannel",
                      "https://t.me:bad/ExampleChannel",
                      "https://t.me/ExampleChannel?a=1",
                      "https://t.me/ExampleChannel#fragment"):
            with self.subTest(value=value):
                self.assertIsNone(parse_public_channel(value))

    def test_scheduled_paid_verified_ready_only_for_manual_action(self):
        report = build_readiness(scheduled_row())
        self.assertTrue(report["ready_for_manual_preparation"])
        self.assertFalse(report["changes_applied"])
        self.assertEqual(report["engine"], "telemt")
        self.assertEqual(report["tag_ending"], TAG[-4:])
        self.assertNotIn(TAG, str(report))
        self.assertIn("Set promotion", str(report["manual_steps"]))

    def test_missing_prerequisites_never_claim_ready(self):
        overrides = [
            ("moderation_status", "pending", "moderation_approved"),
            ("payment_status", "awaiting_payment", "payment_verified"),
            ("placement_status", "not_scheduled", "placement_scheduled"),
            ("node_status", "paused", "node_active"),
            ("telegram_channel_id", None, "channel_verified"),
            ("ad_tag", None, "tag_registered"),
            ("scheduled_end_at", datetime.now(timezone.utc) - timedelta(minutes=1),
             "window_not_expired"),
        ]
        for field, value, failed in overrides:
            with self.subTest(field=field):
                row = {**scheduled_row(), field: value}
                report = build_readiness(row)
                self.assertFalse(report["ready_for_manual_preparation"])
                self.assertFalse(report["checks"][failed])
                self.assertFalse(report["changes_applied"])


if __name__ == "__main__":
    unittest.main()
