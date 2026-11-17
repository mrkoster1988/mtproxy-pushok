"""Реальные flock: диагностика не спорит с автоматикой, изменения сериализованы."""
import fcntl
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import promotion_agent as promotion
import proxy_locks as locks
import proxy_provisioner as host
import updater


class ProxyLocksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.object(updater, 'CONTROL', self.root)
        patcher.start(); self.addCleanup(patcher.stop)

    def assert_runtime_held(self):
        with self.assertRaises(locks.RuntimeBusy) as error:
            with locks.runtime_lock():
                self.fail('another mutation entered')
        self.assertEqual(error.exception.code, 'agent_busy')
        with updater.lock_file('host-lock') as gate:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_diagnostics_reads_during_runtime_but_updater_and_other_mutations_wait(self):
        with locks.runtime_lock(), patch.object(host.subprocess, 'run', return_value=NS(stdout='{}')) as run:
            self.assert_runtime_held()
            self.assertEqual(host.exchange(dict(action='inspect',host='8.8.8.8')), {})
            run.assert_called_once()
        with updater.lock_file('host-lock') as gate:
            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_updater_excludes_both_runtime_and_diagnostics(self):
        with updater.lock_file('host-lock') as gate, patch.object(host.subprocess, 'run') as run:
            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(locks.RuntimeBusy) as error:
                with locks.runtime_lock():
                    self.fail('runtime entered during update')
            self.assertEqual(error.exception.code, 'waiting_update')
            with self.assertRaises(BlockingIOError):host.exchange(dict(action='inspect',host='8.8.8.8'))
            run.assert_not_called()
        with locks.runtime_lock():pass

    def test_wait_releases_gate_and_enters_once_when_short_operation_finishes(self):
        with updater.lock_file('proxy-operation-lock') as operation:
            fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release(_):
                # Ожидающий агент не держит shared-замок обновлятора между попытками.
                with updater.lock_file('host-lock') as gate:
                    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(operation, fcntl.LOCK_UN)
            with patch.object(locks.time, 'sleep', side_effect=release) as sleep:
                entered=0
                with locks.runtime_lock(wait_seconds=1):
                    entered+=1; self.assert_runtime_held()
            self.assertEqual(entered,1); sleep.assert_called_once()

    def test_wait_is_bounded_and_does_not_enter_operation_on_timeout(self):
        with updater.lock_file('proxy-operation-lock') as operation:
            fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(locks.time, 'monotonic', side_effect=[100,100.1,100.5]), \
                 patch.object(locks.time, 'sleep') as sleep:
                with self.assertRaises(locks.RuntimeBusy) as error:
                    with locks.runtime_lock(wait_seconds=0.5):self.fail('entered on timeout')
            self.assertEqual(error.exception.code,'agent_busy'); sleep.assert_called_once_with(0.2)
            with updater.lock_file('host-lock') as gate:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_exception_in_body_is_not_retried_and_releases_all_locks(self):
        with patch.object(locks.time, 'sleep') as sleep:
            with self.assertRaisesRegex(BlockingIOError,'inside operation'):
                with locks.runtime_lock(wait_seconds=1):raise BlockingIOError('inside operation')
            sleep.assert_not_called()
        with locks.runtime_lock():pass

    def test_promotion_entrypoint_allows_diagnostics_while_excluding_mutations(self):
        def tick(status):
            self.assert_runtime_held()
            self.assertEqual(host.exchange(dict(action='inspect',host='8.8.8.8')), {})
            status['code']='no_current_campaign'
        with patch.object(promotion.os,'geteuid',return_value=0), patch.object(promotion,'ENV') as env, \
             patch.object(promotion,'open',side_effect=lambda *a,**kw:tempfile.TemporaryFile(mode='w+'),create=True), \
             patch.object(promotion,'restore_pending_or_expired'), patch.object(promotion,'tick',side_effect=tick), \
             patch.object(host.subprocess,'run',return_value=NS(stdout='{}')):
            env.exists.return_value=True
            env.lstat.return_value=NS(st_mode=0o100600,st_uid=0)
            status={}; promotion.main(status)
        self.assertEqual(status['code'],'no_current_campaign')

    def test_provisioner_keeps_one_operation_lock_from_recovery_through_claim(self):
        (self.root/'mtproxy.env').write_text('MT_PROXY_HOST=8.8.8.8\n')
        actions=[]
        def exchange(request):
            self.assert_runtime_held(); actions.append(request['action'])
            if request['action']=='settings':return dict(enabled=True,first_port=9443,max_instances=2)
            return dict(state='wait',reason='no_demand')
        with patch.object(host.os,'geteuid',return_value=0), patch.object(host,'BASE',self.root), \
             patch.object(host,'primary_host',return_value='8.8.8.8'), \
             patch.object(host,'open',side_effect=lambda *a,**kw:tempfile.TemporaryFile(mode='w+'),create=True), \
             patch.object(host,'reconcile',side_effect=lambda _:self.assert_runtime_held()), \
             patch.object(host,'exchange',side_effect=exchange), patch.object(host,'private_read',return_value='MT_PROXY_HOST=8.8.8.8\n'), \
             patch.object(host,'resource_ready',return_value=True), patch.object(host,'free_port',return_value=True), \
             patch('proxy_readiness.native_ready',return_value=True), patch('proxy_readiness.timers_ready',return_value=True), \
             patch('automatic_binding.connection_status',return_value=dict(enabled=True,helper_available=True,session_saved=True,state='connected')):
            status={}; host.tick(status)
        self.assertEqual(actions,['settings','claim']); self.assertEqual(status['code'],'no_demand')

    def test_promotion_contention_does_not_start_tick_or_claim(self):
        with locks.runtime_lock(), patch.object(promotion.os,'geteuid',return_value=0), \
             patch.object(promotion,'ENV') as env, patch.object(promotion,'tick') as tick, \
             patch.object(promotion,'open',side_effect=lambda *a,**kw:tempfile.TemporaryFile(mode='w+'),create=True), \
             patch.object(promotion,'restore_pending_or_expired'):
            env.exists.return_value=True; env.lstat.return_value=NS(st_mode=0o100600,st_uid=0)
            status={}
            with self.assertRaises(locks.RuntimeBusy):promotion.main(status)
        self.assertEqual(status['code'],'agent_busy'); tick.assert_not_called()

    def test_provisioner_timeout_does_not_reconcile_claim_or_spend_attempts(self):
        (self.root/'mtproxy.env').write_text('MT_PROXY_HOST=8.8.8.8\n')
        with locks.runtime_lock(), patch.object(host.os,'geteuid',return_value=0), \
             patch.object(host,'BASE',self.root), patch.object(host,'primary_host',return_value='8.8.8.8'), \
             patch.object(host,'open',side_effect=lambda *a,**kw:tempfile.TemporaryFile(mode='w+'),create=True), \
             patch.object(host,'reconcile') as reconcile, patch.object(host,'exchange') as exchange, \
             patch.object(locks.time,'monotonic',side_effect=[100,130]):
            with self.assertRaises(locks.RuntimeBusy):host.tick({})
        reconcile.assert_not_called(); exchange.assert_not_called()
