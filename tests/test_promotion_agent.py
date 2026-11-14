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

    def test_expired_plan_rejected(self):
        for changes in [dict(expires_at=time.time()-1),dict(tag='0'*32),dict(revision='bad')]:
            with self.assertRaises(ValueError):agent.validate_plan(dict(self.plan,**changes))

    def test_restart_failure_restores_original_file(self):
        original=self.env.read_text()
        with patch.object(agent,'running_tag',return_value='a'*32),patch.object(agent,'command',side_effect=[RuntimeError('restart'), '']):
            with self.assertRaises(RuntimeError):agent.apply('b'*32)
        self.assertEqual(self.env.read_text(),original)

    def test_same_config_and_process_are_idempotent(self):
        with patch.object(agent,'running_tag',return_value='a'*32),patch.object(agent,'command') as cmd:
            agent.apply('a'*32);cmd.assert_not_called()

    def test_stopped_process_with_same_tag_is_restarted(self):
        with patch.object(agent,'running_tag',side_effect=[FileNotFoundError(), 'a'*32]),patch.object(agent,'command') as cmd,patch.object(agent.socket,'create_connection'):
            agent.apply('a'*32)
            self.assertEqual(cmd.call_args_list[0].args,('systemctl','restart',agent.SERVICE))
