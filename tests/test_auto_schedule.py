from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from app.auto_schedule import earliest_gap
class ScheduleTests(unittest.TestCase):
    def test_full_duration_and_touching_boundaries(self):
        t=datetime(2026,1,1,tzinfo=timezone.utc);hour=timedelta(hours=1)
        self.assertEqual(earliest_gap(t,hour,[(t+hour,t+2*hour)]),t)
        self.assertEqual(earliest_gap(t,2*hour,[(t+hour,t+2*hour),(t+3*hour,t+4*hour)]),t+4*hour)
    def test_nested_expired_and_unsorted_ranges(self):
        t=datetime(2026,1,1,tzinfo=timezone.utc);h=timedelta(hours=1)
        self.assertEqual(earliest_gap(t,h,[(t+h,t+2*h),(t-h,t+3*h),(t-5*h,t-4*h)]),t+3*h)
