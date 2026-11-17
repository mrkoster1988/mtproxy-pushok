"""Потеря ACK, перезапуск процесса и отключение политики без повторного /newproxy."""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy_provisioner as host


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.instances=Path(temp.name)/'instances';self.instances.mkdir(mode=0o700)
        self.job=dict(job_id=str(uuid4()),node_id=str(uuid4()),host='8.8.8.8',port=9443)
        self.cfg=self.job|dict(secret='b'*32,stats_port=20000)
        (self.instances/self.job['node_id']).mkdir(mode=0o700)
        self.receipt=self.instances/self.job['node_id']/'completion.json'
        original_stat=Path.stat
        def root_stat(path,*args,**kwargs):
            info=list(original_stat(path,*args,**kwargs));info[4]=0
            return os.stat_result(info)
        for patcher in (patch.object(host,'INSTANCES',self.instances),patch.object(Path,'stat',root_stat),
                        patch.object(host,'read_instance',return_value=self.cfg),
                        patch.object(host,'private_read',side_effect=lambda p:p.read_text())):
            patcher.start();self.addCleanup(patcher.stop)
        self.real_stop=host.stop_instance
        self.stop=self.patch('stop_instance')
        self.prepared=self.patch('prepared',return_value=True)
        self.exchange=self.patch('exchange')
        telegram=patch('automatic_binding.helper');self.telegram=telegram.start();self.addCleanup(telegram.stop)
        host.save_receipt(self.job,'c'*32)

    def patch(self,name,**kwargs):
        patcher=patch.object(host,name,**kwargs);value=patcher.start();self.addCleanup(patcher.stop);return value

    def test_restart_completes_ready_instance_from_private_receipt(self):
        self.assertEqual(self.receipt.stat().st_mode&0o777,0o600)
        self.exchange.side_effect=[dict(state='pending',job_id=self.job['job_id']),dict(accepted=True)]
        host.reconcile(self.job['host'])
        self.assertEqual(self.exchange.call_args.args[0],dict(action='complete',host=self.job['host'],job_id=self.job['job_id'],tag='c'*32,ready=True))
        self.assertFalse(self.receipt.exists());self.stop.assert_not_called();self.telegram.assert_not_called()

    def test_ack_already_committed_keeps_active_instance_even_when_policy_disabled(self):
        self.exchange.return_value=dict(state='active',job_id=self.job['job_id'])
        host.reconcile(self.job['host'])
        self.assertFalse(self.receipt.exists());self.stop.assert_not_called();self.prepared.assert_not_called()
        self.telegram.assert_not_called();self.assertEqual(self.exchange.call_count,1)

    def test_interrupted_preparation_stops_only_unaccepted_uuid_and_preserves_manifest(self):
        for state in ('pending','blocked'):
            self.stop.reset_mock();self.receipt.unlink(missing_ok=True)
            self.exchange.side_effect=[dict(state=state,job_id=self.job['job_id']),dict(accepted=True)]
            host.reconcile(self.job['host'])
            self.stop.assert_called_once_with(self.job['node_id'])
            self.assertEqual(self.exchange.call_args.args[0]['action'],'interrupted')
            self.assertTrue(self.receipt.parent.exists())
        self.telegram.assert_not_called()

    def test_no_active_service_after_reboot_discards_uncommitted_receipt(self):
        self.prepared.return_value=False
        self.exchange.side_effect=[dict(state='pending',job_id=self.job['job_id']),dict(accepted=True)]
        host.reconcile(self.job['host'])
        self.stop.assert_called_once_with(self.job['node_id']);self.assertFalse(self.receipt.exists())

    def test_unknown_backend_or_lost_reply_keeps_service_and_receipt_for_recheck(self):
        self.exchange.return_value=dict(state='unknown')
        with self.assertRaisesRegex(host.ProvisionError,'recovery_pending'):host.reconcile(self.job['host'])
        self.exchange.side_effect=OSError('connection lost')
        with self.assertRaises(OSError):host.reconcile(self.job['host'])
        self.stop.assert_not_called();self.assertTrue(self.receipt.exists());self.telegram.assert_not_called()

    def test_policy_race_during_complete_waits_for_authoritative_recovery(self):
        self.exchange.side_effect=[dict(state='pending',job_id=self.job['job_id']),dict(accepted=False)]
        with self.assertRaisesRegex(host.ProvisionError,'recovery_pending'):host.reconcile(self.job['host'])
        self.stop.assert_not_called();self.assertTrue(self.receipt.exists())

    def test_definitively_rejected_tag_does_not_leave_a_service_pending_forever(self):
        self.exchange.side_effect=[dict(state='pending',job_id=self.job['job_id']),
            dict(accepted=False,reason='registration_conflict'),dict(accepted=True)]
        host.reconcile(self.job['host'])
        self.stop.assert_called_once_with(self.job['node_id']);self.assertFalse(self.receipt.exists())
        self.assertEqual(self.exchange.call_args.args[0]['code'],'registration_conflict')
        self.telegram.assert_not_called()

    def test_receipt_from_another_job_cannot_activate_node(self):
        self.receipt.write_text(json.dumps(dict(job_id=str(uuid4()),tag='c'*32)))
        self.exchange.return_value=dict(state='pending',job_id=self.job['job_id'])
        with self.assertRaisesRegex(ValueError,'invalid_receipt'):host.reconcile(self.job['host'])
        self.assertEqual(self.exchange.call_count,1);self.stop.assert_not_called()

    def test_service_stop_is_verified_before_clearing_published_link(self):
        root=self.instances.parent;(root/'.control').mkdir()
        link=root/'.control'/('proxy-link-'+self.job['node_id']+'.json');link.write_text('{}')
        stop=self.real_stop
        with patch.object(host,'ROOT',root),patch.object(host.subprocess,'run',return_value=NS(returncode=0,stdout='LoadState=loaded\nActiveState=active')):
            with self.assertRaisesRegex(RuntimeError,'start_failed'):stop(self.job['node_id'])
        self.assertTrue(link.exists())
        with patch.object(host,'ROOT',root),patch.object(host.subprocess,'run',return_value=NS(returncode=0,stdout='LoadState=loaded\nActiveState=inactive')) as run:
            stop(self.job['node_id'])
        self.assertFalse(link.exists())
        stopped=[call.args[0][-1] for call in run.call_args_list if 'disable' in call.args[0]]
        self.assertEqual(len(stopped),3)
        self.assertTrue(all(self.job['node_id'] in name for name in stopped))

    def test_missing_units_after_interrupted_install_are_already_stopped(self):
        with patch.object(host,'ROOT',self.instances.parent),patch.object(host.subprocess,'run',return_value=NS(returncode=1,stdout='LoadState=not-found\nActiveState=inactive')):
            self.real_stop(self.job['node_id'])

    def test_tick_reconciles_when_disabled_and_does_not_spend_attempts_during_cooldown(self):
        base=self.instances.parent;(base/'mtproxy.env').write_text('MT_PROXY_HOST=8.8.8.8\n')
        for enabled,expected in ((False,'disabled'),(True,'telegram_flood_wait')):
            self.exchange.reset_mock();self.exchange.return_value=dict(enabled=enabled,first_port=9443,max_instances=2)
            with patch.object(host.os,'geteuid',return_value=0),patch.object(host,'BASE',base),\
                 patch.object(host,'primary_host',return_value=self.job['host']),\
                 patch.object(host,'open',side_effect=lambda *a,**k:tempfile.TemporaryFile(mode='w+'),create=True),\
                 patch('updater.lock_file',side_effect=lambda _:tempfile.TemporaryFile(mode='w+')),\
                 patch.object(host,'reconcile') as reconcile,patch.object(host,'resource_ready') as resources,\
                 patch('automatic_binding.connection_status',return_value=dict(enabled=True,helper_available=True,session_saved=True,state='connected',retry_seconds=3600)):
                status={};host.tick(status)
            self.assertEqual(status['code'],expected)
            reconcile.assert_called_once_with(self.job['host']);resources.assert_not_called()
            self.assertEqual([c.args[0]['action'] for c in self.exchange.call_args_list],['settings'])
            if enabled:self.assertIn('retry_at',status)
