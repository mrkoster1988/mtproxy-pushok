"""Публичное состояние не подменяет подтверждение агента часами или наблюдением."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app.promotion_runtime import campaign_automation_status


class CampaignAutomationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, 10, 36, tzinfo=timezone.utc)
        self.campaign = dict(placement_status='scheduled', moderation_status='approved',
            payment_status='verified', auto_schedule_paused=False, prepared=True,
            scheduled_start_at=self.now, scheduled_end_at=self.now+timedelta(hours=1))
        self.confirmed = dict(config_applied=False, agent_recent=True)
        self.node = dict(status='active', archived_at=None, target_engine='official_mtproxy')

    def state(self, **changes):
        return campaign_automation_status(dict(self.campaign, **changes), self.confirmed,
                                          self.node, now=self.now)

    def test_time_boundaries_require_receipt(self):
        self.assertEqual(self.state(scheduled_start_at=self.now+timedelta(seconds=1)), 'scheduled')
        self.assertEqual(self.state(), 'starting')
        self.assertEqual(self.state(placement_status='active'), 'awaiting_confirmation')
        self.confirmed['config_applied'] = True
        self.assertEqual(self.state(placement_status='active'), 'active')
        self.assertEqual(self.state(placement_status='active', scheduled_end_at=self.now), 'finishing')
        self.assertEqual(self.state(scheduled_end_at=self.now), 'start_missed')
        self.assertEqual(self.state(placement_status='completed'), 'completed')

    def test_no_agent_is_not_running_or_manual_confirmation(self):
        self.confirmed['agent_recent'] = False
        self.assertEqual(self.state(), 'agent_unavailable')
        self.assertEqual(self.state(placement_status='active'), 'agent_unavailable')
        self.assertEqual(self.state(scheduled_start_at=self.now+timedelta(minutes=1)), 'scheduled')

    def test_eligibility_and_preparation_are_distinct(self):
        for changes, expected in [
            (dict(placement_status='cancelled'), 'cancelled'),
            (dict(moderation_status='pending'), 'awaiting_moderation'),
            (dict(payment_status='awaiting_payment'), 'awaiting_payment'),
            (dict(placement_status='not_scheduled',auto_schedule_paused=True), 'paused'),
            (dict(placement_status='not_scheduled'), 'awaiting_slot'),
            (dict(placement_status='not_scheduled',prepared=False), 'awaiting_slot'),
            (dict(prepared=False), 'setup_required'),
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(self.state(**changes), expected)
        for changes in [dict(status='paused'),dict(archived_at=self.now),dict(target_engine='telemt')]:
            with self.subTest(node=changes):
                self.node.update(changes)
                self.assertEqual(self.state(), 'node_unavailable')


class WaitingReasonTests(unittest.TestCase):
    def test_empty_node_is_idle_and_future_missing_binding_waits(self):
        from unittest.mock import MagicMock
        from app.promotion_runtime import waiting_reason
        conn=MagicMock();conn.execute.return_value.fetchone.return_value=None
        self.assertEqual(waiting_reason(conn,{'id':'node'}),{'reason':'no_current_campaign'})
        row=dict(id='campaign',ended=False,channel_archived=None,moderation_status='approved',payment_status='verified',future=True,scheduled_start_at=datetime.now(timezone.utc),bound_channel=None,binding_current=False)
        conn.execute.return_value.fetchone.return_value=row
        self.assertEqual(waiting_reason(conn,{'id':'node'})['reason'],'waiting_start')
        row['future']=False
        self.assertEqual(waiting_reason(conn,{'id':'node'})['reason'],'binding_missing')
