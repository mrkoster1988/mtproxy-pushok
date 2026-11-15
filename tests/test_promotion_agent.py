"""Изоляция хостового агента: реальные файлы во временном каталоге, systemd замокан."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import promotion_agent as agent

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=Path(self.tmp.name)/'proxy.env';self.state=Path(self.tmp.name)/'state.json'
        self.env.write_text('MT_PROXY_HOST=45.90.57.136\nMT_PROXY_PORT=8443\nMT_PROXY_SECRET=keep-this\nMT_PROXY_TAG="'+('a'*32)+'"\n')
        self.patches=[patch.object(agent,'ENV',self.env),patch.object(agent,'STATE',self.state)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.plan=dict(state='apply',tag='b'*32,expires_at=time.time()+60,revision='c'*64,node_id=str(uuid4()))

    def test_default_sponsor_has_no_paid_period_and_restores_when_disabled(self):
        plan=dict(state='default',tag='d'*32,revision='e'*64,node_id=str(uuid4()),reason='default_sponsor')
        agent.validate_plan(plan)
        with patch.object(agent,'exchange',side_effect=[plan,{'accepted':True}]),patch.object(agent,'apply') as apply:
            agent.tick()
            apply.assert_called_once_with('d'*32)
        self.assertGreater(json.loads(self.state.read_text())['expires_at'],time.time()+366*86400)
        idle=dict(state='idle',revision='f'*64)
        with patch.object(agent,'exchange',side_effect=[idle,{'accepted':True}]),patch.object(agent,'apply') as apply:
            agent.tick()
            apply.assert_called_once_with('a'*32)
        self.assertFalse(self.state.exists())

    def test_only_tag_changes_and_shell_injection_rejected(self):
        original=self.env.read_text()
        self.assertIn('MT_PROXY_SECRET=keep-this',agent.tag_text(original,'b'*32))
        for bad in ['$(id)','a'*31,'a'*32+'\nX=y']:
            with self.assertRaises(ValueError):agent.tag_text(original,bad)
        with self.assertRaises(ValueError):agent.tag_text(original+'MT_PROXY_TAG=x\n','b'*32)

    def test_apply_ack_saves_baseline_before_mutation(self):
        status = {}
        def apply_tag(tag):
            saved = json.loads(self.state.read_text())
            self.assertEqual(saved['baseline'], 'a'*32)
            self.assertTrue(saved['restore_required'])
        with patch.object(agent,'exchange',side_effect=[self.plan,{'accepted':True}]),patch.object(agent,'apply',side_effect=apply_tag) as apply:
            agent.tick(status)
            self.assertEqual(json.loads(self.state.read_text())['baseline'],'a'*32)
            self.assertFalse(json.loads(self.state.read_text())['restore_required'])
            apply.assert_called_once_with('b'*32)
            self.assertEqual(status['node_id'], self.plan['node_id'])
            self.assertEqual(status['plan_revision'], self.plan['revision'])

    def test_failed_switch_returns_to_baseline_not_previous_sponsor(self):
        self.env.write_text(self.env.read_text().replace('"'+('a'*32)+'"', 'b'*32))
        self.state.write_text(json.dumps(dict(baseline='a'*32, expires_at=time.time()+600)))
        next_plan = dict(self.plan, tag='c'*32, expires_at=time.time()+3600)
        process = {'tag': 'b'*32}
        def systemctl(*args):
            if args == ('systemctl', 'restart', agent.SERVICE):
                process['tag'] = agent.parse_env(self.env.read_text())['MT_PROXY_TAG']
            return ''
        status = {}
        with patch.object(agent, 'exchange', return_value=next_plan) as exchange, \
             patch.object(agent, 'running_tag', side_effect=lambda: process['tag']), \
             patch.object(agent, 'command', side_effect=systemctl), \
             patch.object(agent, 'wait_ready', side_effect=[RuntimeError('new tag failed'), None]):
            with self.assertRaisesRegex(RuntimeError, 'new tag failed'):
                agent.tick(status)
        self.assertEqual(exchange.call_count, 1)  # Не подтверждаем неудачное применение.
        self.assertEqual(process['tag'], 'a'*32)
        self.assertEqual(agent.parse_env(self.env.read_text())['MT_PROXY_TAG'], 'a'*32)
        self.assertIn('MT_PROXY_SECRET=keep-this', self.env.read_text())
        self.assertFalse(self.state.exists())
        self.assertEqual(status['code'], 'apply_failed')

    def test_failed_baseline_restore_is_retried_before_offline_api(self):
        self.state.write_text(json.dumps(dict(baseline='d'*32, expires_at=time.time()+600)))
        status = {}
        with patch.object(agent, 'exchange', return_value=self.plan), \
             patch.object(agent, 'apply', side_effect=[RuntimeError('apply failed'), RuntimeError('restore failed')]):
            with self.assertRaisesRegex(RuntimeError, 'restore failed'):
                agent.tick(status)
        self.assertTrue(json.loads(self.state.read_text())['restore_required'])
        self.assertEqual(status['code'], 'restore_failed')
        with patch.object(agent, 'apply') as apply, \
             patch.object(agent, 'exchange', side_effect=RuntimeError('api unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'api unavailable'):
                agent.tick()
        apply.assert_called_once_with('d'*32)
        self.assertFalse(self.state.exists())

    def test_interrupted_apply_is_restored_on_next_cycle(self):
        def interrupted(tag):
            self.env.write_text(agent.tag_text(self.env.read_text(), tag))
            raise KeyboardInterrupt()
        with patch.object(agent, 'exchange', return_value=self.plan), \
             patch.object(agent, 'apply', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                agent.tick()
        self.assertEqual(agent.parse_env(self.env.read_text())['MT_PROXY_TAG'], 'b'*32)
        self.assertTrue(json.loads(self.state.read_text())['restore_required'])
        def restored(tag):
            self.env.write_text(agent.tag_text(self.env.read_text(), tag))
        with patch.object(agent, 'apply', side_effect=restored), \
             patch.object(agent, 'exchange', side_effect=RuntimeError('api unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'api unavailable'):
                agent.tick()
        self.assertEqual(agent.parse_env(self.env.read_text())['MT_PROXY_TAG'], 'a'*32)
        self.assertFalse(self.state.exists())

    def test_applied_unexpired_tag_survives_temporary_api_failure(self):
        for saved in [dict(baseline='a'*32, expires_at=time.time()+600),
                      dict(baseline='a'*32, expires_at=time.time()+600, restore_required=False)]:
            self.state.write_text(json.dumps(saved))
            with patch.object(agent, 'apply') as apply, \
                 patch.object(agent, 'exchange', side_effect=RuntimeError('api unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'api unavailable'):
                    agent.tick()
            apply.assert_not_called()
            self.assertEqual(json.loads(self.state.read_text()), saved)

    def test_pending_restore_precedes_busy_updater(self):
        self.state.write_text(json.dumps(dict(baseline='a'*32, expires_at=time.time()+600,
                                             restore_required=True)))
        status = {}
        with patch.object(agent.os, 'geteuid', return_value=0), \
             patch.object(agent, 'ENV') as env, patch('builtins.open', mock_open()), \
             patch.object(agent.fcntl, 'flock'), \
             patch('updater.lock_file', side_effect=BlockingIOError('update running')), \
             patch.object(agent, 'apply') as apply, patch.object(agent, 'exchange') as exchange:
            env.exists.return_value = True
            env.lstat.return_value = SimpleNamespace(st_mode=0o100600, st_uid=0)
            with self.assertRaises(BlockingIOError):
                agent.main(status)
        apply.assert_called_once_with('a'*32)
        exchange.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertEqual(status['code'], 'waiting_update')

    def test_failed_success_checkpoint_restores_baseline(self):
        atomic = agent.atomic
        writes = []
        def save(path, text):
            writes.append(json.loads(text))
            if len(writes) == 2:
                raise OSError('checkpoint unavailable')
            return atomic(path, text)
        with patch.object(agent, 'atomic', side_effect=save), \
             patch.object(agent, 'exchange', return_value=self.plan) as exchange, \
             patch.object(agent, 'apply') as apply:
            with self.assertRaisesRegex(OSError, 'checkpoint unavailable'):
                agent.tick()
        self.assertEqual([c.args[0] for c in apply.call_args_list], ['b'*32, 'a'*32])
        self.assertEqual(exchange.call_count, 1)
        self.assertFalse(self.state.exists())

    def test_failed_restore_after_cancel_or_rejected_ack_retries_offline(self):
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                self.state.write_text(json.dumps(dict(baseline='d'*32, expires_at=time.time()+600)))
                replies = [dict(state='idle', revision='e'*64)] if cancelled else [self.plan, {'accepted': False}]
                results = [RuntimeError('restore failed')] if cancelled else [None, RuntimeError('restore failed')]
                status = {}
                with patch.object(agent, 'exchange', side_effect=replies), \
                     patch.object(agent, 'apply', side_effect=results):
                    with self.assertRaisesRegex(RuntimeError, 'restore failed'):
                        agent.tick(status)
                self.assertTrue(json.loads(self.state.read_text())['restore_required'])
                self.assertEqual(status['code'], 'restore_failed')
                with patch.object(agent, 'apply') as apply, \
                     patch.object(agent, 'exchange', side_effect=RuntimeError('api unavailable')):
                    with self.assertRaisesRegex(RuntimeError, 'api unavailable'):
                        agent.tick()
                apply.assert_called_once_with('d'*32)
                self.assertFalse(self.state.exists())

    def test_expiration_restores_even_when_api_down(self):
        self.state.write_text(json.dumps(dict(baseline='a'*32,expires_at=time.time()-1)))
        with patch.object(agent,'exchange',side_effect=RuntimeError),patch.object(agent,'apply') as apply:
            with self.assertRaises(RuntimeError):agent.tick()
            apply.assert_called_once_with('a'*32)
        self.assertFalse(self.state.exists())

    def test_changed_plan_rolls_back(self):
        with patch.object(agent,'exchange',side_effect=[self.plan,{'accepted':False}]),patch.object(agent,'apply') as apply:
            with self.assertRaises(RuntimeError):agent.tick()
            self.assertEqual([c.args[0] for c in apply.call_args_list],['b'*32,'a'*32])
        self.assertFalse(self.state.exists())

    def test_switch_preserves_original_baseline(self):
        self.state.write_text(json.dumps(dict(baseline='d'*32,expires_at=time.time()+200)))
        with patch.object(agent,'exchange',side_effect=[self.plan,{'accepted':True}]),patch.object(agent,'apply'):
            agent.tick()
        self.assertEqual(json.loads(self.state.read_text())['baseline'],'d'*32)

    def test_idle_unmanaged_does_not_restart(self):
        with patch.object(agent,'exchange',side_effect=[dict(state='idle',revision='c'*64),{'accepted':True}]),patch.object(agent,'apply') as apply:
            agent.tick();apply.assert_not_called()

    def test_domain_tick_preserves_identity_for_plan_and_ack(self):
        self.env.write_text(self.env.read_text().replace('45.90.57.136', 'MtProxy.Example.org'))
        with patch.object(agent.socket, 'gethostbyname', return_value='203.0.113.77'), \
             patch.object(agent, 'exchange', side_effect=[self.plan, {'accepted': True}]) as exchange, \
             patch.object(agent, 'apply') as apply:
            agent.tick()
        request = exchange.call_args_list[0].args[0]
        self.assertEqual(request, dict(host='mtproxy.example.org', port=8443, resolved_host='203.0.113.77'))
        self.assertEqual(exchange.call_args_list[1].args[0], dict(request, action='ack', revision=self.plan['revision']))
        apply.assert_called_once_with('b'*32)

    def test_ip_identity_does_not_require_dns(self):
        with patch.object(agent.socket, 'gethostbyname') as resolve:
            self.assertEqual(agent.node_request(agent.parse_env(self.env.read_text())),
                             dict(host='45.90.57.136', port=8443))
        resolve.assert_not_called()

    def test_dns_failure_keeps_exact_domain_lookup(self):
        cfg = dict(MT_PROXY_HOST='mtproxy.example.org', MT_PROXY_PORT='8443')
        with patch.object(agent.socket, 'gethostbyname', side_effect=OSError):
            self.assertEqual(agent.node_request(cfg), dict(host='mtproxy.example.org', port=8443))

    def test_full_tick_applies_and_restores_files_after_cancel(self):
        process = {'tag': 'a'*32}
        def systemctl(*args):
            if args == ('systemctl', 'restart', agent.SERVICE):
                process['tag'] = agent.parse_env(self.env.read_text())['MT_PROXY_TAG']
            return ''
        idle = dict(state='idle', revision='d'*64)
        with patch.object(agent, 'exchange', side_effect=[self.plan, {'accepted': True}, idle, {'accepted': True}]), \
             patch.object(agent, 'running_tag', side_effect=lambda: process['tag']), \
             patch.object(agent, 'command', side_effect=systemctl), \
             patch.object(agent.socket, 'create_connection'):
            agent.tick()
            self.assertEqual(process['tag'], 'b'*32)
            self.assertEqual(agent.parse_env(self.env.read_text())['MT_PROXY_TAG'], 'b'*32)
            self.assertEqual(json.loads(self.state.read_text())['baseline'], 'a'*32)
            agent.tick()
        self.assertEqual(process['tag'], 'a'*32)
        self.assertEqual(agent.parse_env(self.env.read_text())['MT_PROXY_TAG'], 'a'*32)
        self.assertIn('MT_PROXY_SECRET=keep-this', self.env.read_text())
        self.assertFalse(self.state.exists())

    def test_expired_plan_rejected(self):
        for changes in [dict(expires_at=time.time()-1),dict(tag='0'*32),dict(revision='bad')]:
            with self.assertRaises(ValueError):agent.validate_plan(dict(self.plan,**changes))

    def test_restart_failure_restores_original_file(self):
        original=self.env.read_text()
        with patch.object(agent,'running_tag',return_value='a'*32),patch.object(agent,'command',side_effect=[RuntimeError('restart'), '']):
            with self.assertRaises(RuntimeError):agent.apply('b'*32)
        self.assertEqual(self.env.read_text(),original)

    def test_same_config_and_process_are_idempotent(self):
        with patch.object(agent,'running_tag',return_value='A'*32),patch.object(agent,'command') as cmd,patch.object(agent.socket,'create_connection') as connect:
            agent.apply('a'*32)
        self.assertFalse(any(c.args[:2]==('systemctl','restart') for c in cmd.call_args_list))
        connect.assert_called_once_with(('45.90.57.136',8443),timeout=1)

    def test_same_tag_unreachable_service_is_restarted_before_ack(self):
        self.env.write_text(self.env.read_text().replace('a'*32, 'b'*32))
        calls = []
        def exchange(request):
            calls.append(request.get('action','plan'))
            return {'accepted':True} if request.get('action')=='ack' else self.plan
        def command(*args):
            if args[:2]==('systemctl','restart'):
                calls.append('restart')
        with patch.object(agent,'running_tag',return_value='b'*32),patch.object(agent,'command',side_effect=command), \
             patch.object(agent,'wait_ready',side_effect=[RuntimeError('proxy_start_timeout'),None]), \
             patch.object(agent,'exchange',side_effect=exchange):
            agent.tick()
        self.assertEqual(calls,['plan','restart','ack'])

    def test_failed_health_and_restart_do_not_ack(self):
        self.env.write_text(self.env.read_text().replace('a'*32, 'b'*32))
        status = {}
        with patch.object(agent,'running_tag',return_value='b'*32),patch.object(agent,'command'), \
             patch.object(agent,'wait_ready',side_effect=RuntimeError('proxy_start_timeout')), \
             patch.object(agent,'exchange',return_value=self.plan) as exchange:
            with self.assertRaisesRegex(RuntimeError,'proxy_start_timeout'):
                agent.tick(status)
        # Недоступен и исходный тег: сохраняем необходимость повторного возврата.
        self.assertEqual(status['code'],'restore_failed')
        self.assertEqual(exchange.call_count,1)
        self.assertTrue(self.state.exists())
        self.assertTrue(json.loads(self.state.read_text())['restore_required'])

    def test_stopped_process_with_same_tag_is_restarted(self):
        with patch.object(agent,'running_tag',side_effect=[FileNotFoundError(), 'a'*32]),patch.object(agent,'command') as cmd,patch.object(agent.socket,'create_connection'):
            agent.apply('a'*32)
            self.assertEqual(cmd.call_args_list[0].args,('systemctl','restart',agent.SERVICE))

    def test_uppercase_baseline_restores_in_installer_compatible_format(self):
        self.state.write_text(json.dumps(dict(baseline='AB'*16,expires_at=time.time()-1)))
        with patch.object(agent,'running_tag',return_value='a'*32),patch.object(agent,'command'),patch.object(agent,'wait_ready'):
            agent.restore()
        self.assertFalse(self.state.exists())
        tag_line=next(s for s in self.env.read_text().splitlines() if s.startswith('MT_PROXY_TAG='))
        self.assertEqual(tag_line,'MT_PROXY_TAG='+'AB'*16)
        self.assertIn('MT_PROXY_SECRET=keep-this',self.env.read_text())

    def test_waits_for_runner_and_tcp_listener(self):
        with patch.object(agent,'command'),patch.object(agent,'running_tag',side_effect=[RuntimeError('runner'), 'b'*32,'b'*32]),patch.object(agent.socket,'create_connection',side_effect=[ConnectionRefusedError(),unittest.mock.MagicMock()]) as connect,patch.object(agent.time,'sleep') as sleep,patch.object(agent.time,'monotonic',side_effect=[0,0,1]):
            agent.wait_ready('B'*32,agent.parse_env(self.env.read_text()))
        self.assertEqual(sleep.call_count,2)
        self.assertEqual(connect.call_count,2)

    def test_readiness_timeout_restores_original_config(self):
        original=self.env.read_text()
        with patch.object(agent,'command') as cmd,patch.object(agent,'running_tag',side_effect=['a'*32,'b'*32]),patch.object(agent.socket,'create_connection',side_effect=ConnectionRefusedError()),patch.object(agent.time,'monotonic',side_effect=[0,16]):
            with self.assertRaisesRegex(RuntimeError,'proxy_start_timeout'):
                agent.apply('b'*32)
        self.assertEqual(self.env.read_text(),original)
        self.assertEqual(sum(c.args==('systemctl','restart',agent.SERVICE) for c in cmd.call_args_list),2)

