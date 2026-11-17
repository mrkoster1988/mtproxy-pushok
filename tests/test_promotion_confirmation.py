"""Подтверждение действительно только для свежего неизменённого плана."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import promotion_runtime as runtime


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'promotion-agent.json'
        env = patch.dict(os.environ, {'PUSHOK_CONTROL_DIR': directory.name})
        env.start(); self.addCleanup(env.stop)
        self.now = datetime.now(timezone.utc)
        self.node_id = str(uuid4())
        self.plan = dict(state='apply', reason='ready', campaign_id='campaign', tag='a'*32,
                         node_id=self.node_id, expires_at=(self.now+timedelta(hours=1)).timestamp())
        self.receipt = dict(state='applied', campaign_id='campaign', checked_at=self.now,
                            plan_revision=runtime.revision(self.plan))
        self.report()

    def report(self, **changes):
        data = dict(code='tag_applied', checked_at=self.now.isoformat(),
                    node_id=self.node_id, plan_revision=self.receipt['plan_revision'])
        data.update(changes)
        self.path.write_text(json.dumps(data))

    def read(self, receipt=None, plan=None):
        with patch.object(runtime, 'node_plan', return_value=plan or self.plan):
            return runtime.confirmation(MagicMock(), {'id':self.node_id}, receipt or self.receipt, now=self.now)

    def test_unchanged_plan_is_confirmed_without_claiming_telegram(self):
        result = self.read()
        self.assertTrue(result['config_applied'])
        self.assertTrue(result['agent_recent'])
        self.assertFalse(result['display_verified'])
        self.assertNotIn('plan_revision', result)

    def test_additional_node_report_is_not_replaced_by_primary_report(self):
        target=self.path.with_name('promotion-agent-'+self.node_id+'.json')
        target.write_text(self.path.read_text())
        self.report(node_id=str(uuid4()),code='apply_failed')
        self.assertTrue(self.read()['config_applied'])
        data=json.loads(target.read_text());data['code']='apply_failed';target.write_text(json.dumps(data))
        self.assertFalse(self.read()['config_applied'])

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

    def test_new_error_or_restore_invalidates_recent_database_success(self):
        receipt = dict(self.receipt, checked_at=self.now-timedelta(seconds=10))
        self.assertTrue(self.read(receipt)['config_applied'])
        for code in ['apply_failed', 'restore_failed', 'ack_unavailable', 'restored',
                     'idle', 'runtime_unavailable', 'waiting_update', 'plan_changed', 'unknown']:
            with self.subTest(code=code):
                self.report(code=code)
                self.assertFalse(self.read(receipt)['config_applied'])

    def test_absent_stale_future_wrong_node_or_revision_is_not_confirmation(self):
        self.path.unlink()
        self.assertFalse(self.read()['config_applied'])
        for changes in [dict(node_id=None), dict(node_id=str(uuid4())), dict(plan_revision=None),
                        dict(plan_revision='f'*64), dict(checked_at=(self.now-timedelta(seconds=120)).isoformat()),
                        dict(checked_at=(self.now+timedelta(seconds=1)).isoformat()),
                        dict(checked_at=(self.now-timedelta(seconds=1)).isoformat())]:
            with self.subTest(changes=changes):
                self.report(**changes)
                self.assertFalse(self.read()['config_applied'])

    def test_confirmation_does_not_expose_internal_report_fields(self):
        text = json.dumps(self.read(), default=str)
        self.assertNotIn(self.plan['tag'], text)
        self.assertNotIn(self.receipt['plan_revision'], text)
        self.assertNotIn(self.node_id, text)
