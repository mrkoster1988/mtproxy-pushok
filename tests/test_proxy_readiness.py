"""Предварительная проверка не меняет очередь, службы или регистрацию Telegram."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy_provisioner as host
import proxy_readiness as readiness
from app import proxy_provisioning as api


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(first_port=9443, max_instances=2, policy='ready', capacity='ready', reserved_ports=[],policy_revision=str(uuid4()))
        self.owner = dict(enabled=True, helper_available=True, session_saved=True, state='connected')
        self.exchange = self.start(patch.object(host, 'exchange', return_value=self.cfg))
        self.start(patch.object(host, 'primary_host', return_value='8.8.8.8'))
        self.resources = self.start(patch.object(host, 'resource_ready', return_value=True))
        self.ports = self.start(patch.object(host, 'free_port', return_value=True))
        self.native = self.start(patch.object(readiness, 'native_ready', return_value=True))
        self.timers = self.start(patch.object(readiness, 'timers_ready', return_value=True))
        self.connection = self.start(patch('automatic_binding.connection_status', return_value=self.owner))
        self.telegram = self.start(patch('automatic_binding.helper'))
        self.provision = self.start(patch.object(host, 'provision'))
        self.commands = self.start(patch.object(host, 'command'))

    def start(self, patcher):
        value = patcher.start(); self.addCleanup(patcher.stop); return value

    def test_ready_report_has_only_fixed_checks_and_no_secret_or_network_operations(self):
        self.owner.update(username='private-name', phone='private-phone', session='secret')
        report = readiness.inspect(host)
        self.assertEqual(report['checks'], {key:'ready' for key in api.READINESS})
        self.assertEqual(report['policy_revision'],self.cfg['policy_revision'])
        self.assertNotIn('private', json.dumps(report)); self.assertNotIn('secret', json.dumps(report))
        self.exchange.assert_called_once_with(dict(action='inspect', host='8.8.8.8'))
        self.telegram.assert_not_called(); self.provision.assert_not_called(); self.commands.assert_not_called()

    def test_multiple_failures_are_reported_independently_without_traceback_secrets(self):
        self.native.return_value=False; self.timers.return_value=False; self.resources.return_value=False
        self.exchange.side_effect=OSError('private database password')
        self.owner['state']='revoked'
        result=readiness.inspect(host)
        for key, value in dict(native='native_unavailable', timers='timer_unavailable', resources='resources_low',
                               api='api_unavailable', telegram='owner_unavailable', policy='unknown').items():
            self.assertEqual(result['checks'][key], value)
        self.assertNotIn('password', json.dumps(result)); self.telegram.assert_not_called()

    def test_cooldown_and_busy_owner_are_not_connected_by_probe(self):
        self.owner['retry_seconds']=3600
        report=readiness.inspect(host)
        self.assertEqual(report['checks']['telegram'], 'telegram_flood_wait')
        self.assertGreater(datetime.fromisoformat(report['retry_at']), datetime.now(timezone.utc)+timedelta(minutes=59))
        self.owner['state']='connection_busy'
        self.assertEqual(readiness.inspect(host)['checks']['telegram'], 'connection_busy')
        self.telegram.assert_not_called()

    def test_reserved_ports_and_stats_ports_are_checked_without_claim(self):
        self.cfg['reserved_ports']=[9443]; self.ports.side_effect=lambda p:p!=9444
        self.assertEqual(readiness.inspect(host)['checks']['ports'], 'port_busy')
        self.assertNotIn(9443, [c.args[0] for c in self.ports.call_args_list])
        self.ports.side_effect=lambda p:p<20000
        self.assertEqual(readiness.inspect(host)['checks']['ports'], 'port_busy')
        self.assertTrue(all(c.args[0]['action']=='inspect' for c in self.exchange.call_args_list))

    def test_existing_preparation_or_full_capacity_is_not_mistaken_for_port_conflict(self):
        for state in ('preparation_pending', 'instance_limit'):
            self.cfg['capacity']=state
            self.assertEqual(readiness.inspect(host)['checks']['ports'], state)
        self.ports.assert_not_called()

    def test_invalid_policy_cannot_expand_port_scan(self):
        for values in ({'max_instances':99999}, {'first_port':True}, {'policy':'<secret>'}, {'reserved_ports':['secret']}):
            with patch.object(host, 'exchange', return_value=self.cfg|values):
                result=readiness.inspect(host)
            self.assertEqual(result['checks']['api'], 'api_unavailable')
        self.ports.assert_not_called()

    def test_cli_check_publishes_report_and_exits_without_tick_or_reconciliation(self):
        for code, expected in (('ready', 0), ('native_unavailable', 2)):
            report={'checks':{'native':code}}
            with patch.object(host.os, 'geteuid', return_value=0), patch.object(host.sys, 'argv', ['proxy_provisioner.py', 'check']), \
                 patch.object(readiness, 'inspect', return_value=report), patch.object(readiness, 'publish') as publish, \
                 patch.object(host, 'tick') as tick, patch.object(host, 'reconcile') as reconcile, patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(host.main(), expected)
                self.assertEqual(json.loads(output.getvalue()), report)
            publish.assert_called_once_with(host, report); tick.assert_not_called(); reconcile.assert_not_called()

    def test_missing_runtime_stops_before_claim_but_after_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); (base/'mtproxy.env').write_text('MT_PROXY_HOST=8.8.8.8\n')
            self.exchange.return_value=dict(enabled=True, first_port=9443, max_instances=2,policy_revision=self.cfg['policy_revision'])
            for failed, expected in ((self.native, 'native_unavailable'), (self.timers, 'timer_unavailable')):
                self.native.return_value=True; self.timers.return_value=True; failed.return_value=False
                self.exchange.reset_mock()
                with patch.object(host.os, 'geteuid', return_value=0), patch.object(host, 'BASE', base), \
                     patch.object(host, 'open', side_effect=lambda *a,**kw:tempfile.TemporaryFile(mode='w+'), create=True), \
                     patch('updater.lock_file', side_effect=lambda _:tempfile.TemporaryFile(mode='w+')), \
                     patch.object(host, 'reconcile') as reconcile:
                    status={}; host.tick(status)
                self.assertEqual(status['code'], expected); reconcile.assert_called_once()
                self.assertEqual(status['policy_revision'],self.cfg['policy_revision'])
                self.assertEqual([c.args[0]['action'] for c in self.exchange.call_args_list], ['settings'])
        self.provision.assert_not_called(); self.telegram.assert_not_called()

    def test_periodic_check_still_runs_when_automation_is_disabled(self):
        report=readiness.inspect(host)
        with patch.object(host.os, 'geteuid', return_value=0), patch.object(host.sys, 'argv', ['proxy_provisioner.py']), \
             patch.object(host, 'tick', side_effect=lambda s:s.update(code='disabled')), \
             patch.object(host, 'publish_report') as execution, patch.object(readiness, 'inspect', return_value=report), \
             patch.object(readiness, 'publish') as publish:
            self.assertEqual(host.main(), 0)
        execution.assert_called_once_with({'code':'disabled'}); publish.assert_called_once_with(host,report)


class RuntimeProbeTests(unittest.TestCase):
    def test_native_check_rejects_missing_symlink_and_writable_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); binary=base/'native'
            original=Path.lstat
            def root_info(path,*args,**kw):
                values=list(original(path,*args,**kw)); values[4]=0; return os.stat_result(values)
            with patch.object(Path,'lstat',root_info):
                self.assertFalse(readiness.native_ready(base,binary))
                for path in (binary,base/'proxy-secret',base/'proxy-multi.conf'):
                    path.write_text('fixture'); path.chmod(0o600)
                binary.chmod(0o700); self.assertTrue(readiness.native_ready(base,binary))
                binary.chmod(0o777); self.assertFalse(readiness.native_ready(base,binary))
                binary.unlink(); binary.symlink_to(base/'proxy-secret')
                self.assertFalse(readiness.native_ready(base,binary))

    def test_service_probe_only_reads_and_requires_persistent_active_timers(self):
        for state, expected in (('enabled',True), ('disabled',False), ('enabled-runtime',False)):
            with patch.object(readiness.subprocess,'run',return_value=NS(returncode=0,
                stdout='LoadState=loaded\nActiveState=active\nUnitFileState='+state)) as run:
                self.assertEqual(readiness.timers_ready(),expected)
            self.assertTrue(all(c.args[0][:2]==['systemctl','show'] for c in run.call_args_list))

    def test_api_report_whitelist_and_freshness(self):
        revision=str(uuid4())
        report=dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(), checks={k:'ready' for k in api.READINESS}, secret='private',policy_revision=revision)
        with patch('app.control.read',return_value=report):
            public=api.readiness_report(revision)
            self.assertTrue(public['recent']); self.assertNotIn('secret',public)
            self.assertTrue(public['current_policy']); self.assertFalse(api.readiness_report(str(uuid4()))['current_policy'])
            report['checked_at']=(datetime.now(timezone.utc)-timedelta(minutes=12)).isoformat()
            self.assertFalse(api.readiness_report()['recent'])
            for bad in ('tomorrow', datetime.now().isoformat(), (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()):
                report['checked_at']=bad; self.assertIsNone(api.readiness_report())
            report['checked_at']=datetime.now(timezone.utc).isoformat()
            report['checks']['host']='secret-token'; self.assertIsNone(api.readiness_report())
        for malformed in ([],None,{}, {'schema':1,'checks':[]}):
            with patch('app.control.read',return_value=malformed):self.assertIsNone(api.readiness_report())

    def test_execution_report_preserves_only_valid_policy_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'.control').mkdir()
            for revision in (str(uuid4()),'secret-in-error',None):
                with patch.object(host,'ROOT',root),patch.object(host.os,'chown'):
                    host.publish_report(dict(code='disabled',policy_revision=revision))
                report=json.loads((root/'.control'/'proxy-provisioner.json').read_text())
                if revision and revision!='secret-in-error':self.assertEqual(report['policy_revision'],revision)
                else:self.assertNotIn('policy_revision',report)
