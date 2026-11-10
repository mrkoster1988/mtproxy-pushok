"""v0.034: контракт ограничения удержания и сроков."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.slot_holds import HOLD_TTL, normalize_hold_window


class SlotHoldsTests(unittest.TestCase):
    def test_hold_is_fifteen_minutes_not_placement_duration(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        start, end, expiry = normalize_hold_window(now + timedelta(days=1), 86400, now)
        self.assertEqual(end - start, timedelta(days=1))
        self.assertEqual(expiry - now, HOLD_TTL)

    def test_naive_past_and_invalid_duration_rejected(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        for start, duration in [
            (datetime(2026, 11, 10), 86400),
            (now, 86400),
            (now - timedelta(seconds=1), 86400),
            (now + timedelta(days=1), 0),
        ]:
            with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                normalize_hold_window(start, duration, now)

    def test_install_and_scheduler_use_same_slot_holds(self):
        schema = (ROOT / "database/010_slot_holds.sql").read_text()
        api = (ROOT / "api/app/main.py").read_text()
        setup = (ROOT / "setup.sh").read_text()
        self.assertIn("slot_holds_no_overlap EXCLUDE USING gist", schema)
        self.assertIn("slot_holds_one_per_campaign UNIQUE", schema)
        self.assertIn("010_slot_holds.sql", setup)
        self.assertIn("DELETE FROM slot_holds WHERE proxy_node_id", api)
        self.assertIn("expires_at > now()", api)
        self.assertIn("slot_held_by_another_campaign", api)


if __name__ == "__main__":
    unittest.main()
