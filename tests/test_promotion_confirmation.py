"""Подтверждение действительно только для свежего неизменённого плана."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import promotion_runtime as runtime


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.plan = dict(state='apply', reason='ready', campaign_id='campaign', tag='a'*32,
                         node_id='node', expires_at=(self.now+timedelta(hours=1)).timestamp())
        self.receipt = dict(state='applied', campaign_id='campaign', checked_at=self.now,
                            plan_revision=runtime.revision(self.plan))

    def read(self, receipt=None, plan=None):
        with patch.object(runtime, 'node_plan', return_value=plan or self.plan):
            return runtime.confirmation(MagicMock(), {'id':'node'}, receipt or self.receipt, now=self.now)

    def test_unchanged_plan_is_confirmed_without_claiming_telegram(self):
        result = self.read()
        self.assertTrue(result['config_applied'])
        self.assertTrue(result['agent_recent'])
        self.assertFalse(result['display_verified'])
        self.assertNotIn('plan_revision', result)

    def test_missing_old_stale_and_future_receipts_are_not_confirmation(self):
        for changes in [dict(plan_revision=None), dict(state='idle'), dict(campaign_id='other'),
                        dict(checked_at=None), dict(checked_at=self.now-timedelta(seconds=120)),
                        dict(checked_at=self.now+timedelta(seconds=1))]:
            with self.subTest(changes=changes):
                self.assertFalse(self.read(dict(self.receipt, **changes))['config_applied'])
        self.assertTrue(self.read(dict(self.receipt,checked_at=self.now-timedelta(seconds=119)))['config_applied'])

    def test_changed_tag_period_campaign_and_idle_invalidate_confirmation(self):
        for changes in [dict(tag='b'*32), dict(expires_at=self.plan['expires_at']+60),
                        dict(campaign_id='other'), dict(state='idle')]:
            with self.subTest(changes=changes):
                self.assertFalse(self.read(plan=dict(self.plan, **changes))['config_applied'])
