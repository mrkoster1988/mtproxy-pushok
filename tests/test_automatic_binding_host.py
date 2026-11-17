"""Прокси снимает старый тег до внешней записи; сбой не теряет блокировку."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import automatic_binding as binding
import promotion_agent


class HostBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.json'
        self.job=dict(state='job',job_id='test',phase='bind',channel_url='https://t.me/example',expected_tag='a'*32)
        self.agent=NS(STATE=self.path,ROOT=Path(self.tmp.name),exchange=Mock(side_effect=[self.job,dict(accepted=True)]),
                      atomic=promotion_agent.atomic,apply=Mock())
        self.request=dict(host='proxy.example.org',port=443)

    def test_disabled_mode_does_not_change_prepared_tag_workflow(self):
        with patch.object(binding,'enabled',return_value=False):binding.tick(self.agent,self.request)
        self.agent.exchange.assert_not_called();self.agent.apply.assert_not_called()

    def test_clear_and_durable_empty_baseline_precede_telegram(self):
        self.path.write_text(json.dumps(dict(baseline='b'*32,expires_at=99999999999)))
        def apply(tag):
            self.assertEqual(tag,'')
            self.assertEqual(json.loads(self.path.read_text())['baseline'],'')
            self.assertTrue(json.loads(self.path.read_text())['restore_required'])
        self.agent.apply.side_effect=apply
        def helper(root,request):
            self.assertFalse(self.path.exists())
            self.agent.apply.assert_called_once_with('')
            return dict(ok=True,tag='a'*32,channel_username='example',owner_telegram_id=1,telegram_channel_id=2,bot_message_id=3)
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'helper',side_effect=helper):
            binding.tick(self.agent,self.request)
        self.assertEqual(self.agent.exchange.call_args.args[0]['action'],'owner_verified')

    def test_proxy_failure_prevents_any_telegram_mutation(self):
        self.agent.apply.side_effect=RuntimeError('local failure')
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'helper') as helper:
            with self.assertRaises(RuntimeError):binding.tick(self.agent,self.request)
        helper.assert_not_called()
        self.assertEqual(self.agent.exchange.call_count,1)

    def test_floodwait_is_reported_and_cannot_be_marked_success(self):
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'helper',return_value=dict(ok=False,code='telegram_flood_wait',retry_seconds=9000)):
            binding.tick(self.agent,self.request)
        report=self.agent.exchange.call_args.args[0]
        self.assertEqual(report['action'],'owner_error');self.assertEqual(report['retry_seconds'],9000)

    def test_waiting_cooldown_does_not_call_telegram(self):
        self.agent.exchange=Mock(return_value=dict(state='wait'))
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'helper') as helper:
            binding.tick(self.agent,self.request)
        helper.assert_not_called();self.agent.apply.assert_not_called()

    def test_lost_result_does_not_erase_server_job(self):
        self.agent.exchange=Mock(side_effect=[self.job,RuntimeError('database disconnected')])
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'helper',return_value=dict(ok=True,tag='a'*32)):
            with self.assertRaises(RuntimeError):binding.tick(self.agent,self.request)
        self.assertEqual(self.agent.exchange.call_args.args[0]['action'],'owner_verified')
        self.assertFalse(self.path.exists())
