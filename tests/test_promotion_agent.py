"""Изоляция хостового агента: реальные файлы во временном каталоге, systemd замокан."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import promotion_agent as agent

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=Path(self.tmp.name)/'proxy.env';self.state=Path(self.tmp.name)/'state.json'
        self.env.write_text('MT_PROXY_HOST=45.90.57.136\nMT_PROXY_PORT=8443\nMT_PROXY_SECRET=keep-this\nMT_PROXY_TAG="'+('a'*32)+'"\n')
        self.patches=[patch.object(agent,'ENV',self.env),patch.object(agent,'STATE',self.state)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.plan=dict(state='apply',tag='b'*32,expires_at=time.time()+60,revision='c'*64)

    def test_only_tag_changes_and_shell_injection_rejected(self):
        original=self.env.read_text()
        self.assertIn('MT_PROXY_SECRET=keep-this',agent.tag_text(original,'b'*32))
        for bad in ['$(id)','a'*31,'a'*32+'\nX=y']:
            with self.assertRaises(ValueError):agent.tag_text(original,bad)
        with self.assertRaises(ValueError):agent.tag_text(original+'MT_PROXY_TAG=x\n','b'*32)

    def test_apply_ack_saves_baseline_before_mutation(self):
        with patch.object(agent,'exchange',side_effect=[self.plan,{'accepted':True}]),patch.object(agent,'apply') as apply:
            agent.tick()
            self.assertEqual(json.loads(self.state.read_text())['baseline'],'a'*32)
            apply.assert_called_once_with('b'*32)

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
        with patch.object(agent,'running_tag',return_value='A'*32),patch.object(agent,'command') as cmd:
            agent.apply('a'*32);cmd.assert_not_called()

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

