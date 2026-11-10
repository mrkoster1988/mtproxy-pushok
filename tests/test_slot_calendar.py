"""v0.033: безопасная классификация спонсорских слотов."""
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.slot_calendar import slot_status


class SlotCalendarTests(unittest.TestCase):
    def test_available_occupied_and_past(self):
        now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        future = now + timedelta(days=1)
        self.assertEqual(slot_status(future, now, 2), "available")
        self.assertEqual(slot_status(future, now, 0), "occupied")
        self.assertEqual(slot_status(now, now, 2), "past")

    def test_dst_gap_and_fold_are_not_bookable(self):
        berlin = ZoneInfo("Europe/Berlin")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        missing = datetime(2026, 3, 29, 2, 30, tzinfo=berlin)
        repeated = datetime(2026, 10, 25, 2, 30, tzinfo=berlin)
        self.assertEqual(slot_status(missing, now, 3), "invalid_local_time")
        self.assertEqual(slot_status(repeated, now, 3), "ambiguous_local_time")
        normal = datetime(2026, 3, 29, 3, 30, tzinfo=berlin)
        self.assertEqual(slot_status(normal, now, 1), "available")

    def test_naive_and_invalid_counts_rejected(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            slot_status(datetime(2026, 11, 10), now, 1)
        with self.assertRaises(ValueError):
            slot_status(now + timedelta(days=1), now, -1)


if __name__ == "__main__":
    unittest.main()
