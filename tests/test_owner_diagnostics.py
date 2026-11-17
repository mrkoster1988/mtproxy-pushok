"""Диагностика подключения не раскрывает сессию и не заменяет проверку показа."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'api')]
import automatic_binding as binding
import telegram_binding_worker as worker
from telegram_owner import PrivateState, record_health
from app.promotion_report import owner_connection

SAVED = dict(api_id=123, api_hash='a'*32, session='PRIVATE-SESSION', user_id=42)


class OwnerHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'owner'
        for p in (patch.object(binding,'DEFAULT_STATE',self.path), patch.object(binding,'PYTHON',sys.executable)):
            p.start();self.addCleanup(p.stop)

    def save(self, state='connected', user_id=42):
        with PrivateState(self.path) as private:
            private.save(SAVED)
            marker=self.path/'automatic.enabled';marker.touch(mode=0o600)
            record_health(private,dict(state=state,telegram_user_id=user_id,username='test_owner',session='PRIVATE-SESSION'))

    def test_no_directory_is_not_connected_and_is_not_created(self):
        self.assertEqual(binding.connection_status()['state'],'not_connected')
        self.assertFalse(self.path.exists())

    def test_connected_report_and_health_file_never_include_credentials(self):
        self.save()
        result=binding.connection_status()
        self.assertEqual(result['state'],'connected');self.assertTrue(result['enabled'])
        self.assertEqual(result['username'],'test_owner')
        raw=(self.path/'health.json').read_text()+json.dumps(result)
        for secret in ('PRIVATE-SESSION', 'api_id', 'api_hash', 'a'*32):self.assertNotIn(secret,raw)
        self.assertEqual((self.path/'health.json').stat().st_mode & 0o777,0o600)

    def test_missing_or_wrong_account_health_is_not_a_live_confirmation(self):
        self.save(user_id=99)
        self.assertEqual(binding.connection_status()['state'],'session_saved')
        (self.path/'health.json').unlink()
        self.assertEqual(binding.connection_status()['state'],'session_saved')
        self.save('revoked')
        self.assertEqual(binding.connection_status()['state'],'revoked')
        self.save()
        (self.path/'automatic.enabled').unlink()
        self.assertFalse(binding.connection_status()['enabled'])
        self.assertTrue(binding.connection_status()['session_saved'])

    def test_unsafe_marker_session_and_concurrent_lock_do_not_look_ready(self):
        self.save()
        with PrivateState(self.path):self.assertEqual(binding.connection_status()['state'],'connection_busy')
        (self.path/'automatic.enabled').chmod(0o644)
        self.assertEqual(binding.connection_status()['state'],'invalid_state')
        (self.path/'automatic.enabled').chmod(0o600)
        (self.path/'owner.json').write_text('null')
        self.assertEqual(binding.connection_status()['state'],'invalid_state')

    def test_disable_between_claim_and_helper_stops_before_client_creation(self):
        import telethon
        self.save();(self.path/'automatic.enabled').unlink()
        output=io.StringIO()
        with patch.object(worker,'DEFAULT_STATE',self.path),patch.object(worker.sys,'stdin',io.StringIO('{"action":"bind"}')), \
                patch.object(telethon,'TelegramClient') as client,redirect_stdout(output):
            previous=os.umask(0o077)
            try:worker.main()
            finally:os.umask(previous)
        client.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()),dict(ok=False,code='automation_disabled',retry_seconds=300))


class OwnerReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'promotion-agent.json'
        p=patch.dict(os.environ,{'PUSHOK_CONTROL_DIR':self.tmp.name});p.start();self.addCleanup(p.stop)
        self.now=datetime.now(timezone.utc)

    def report(self, owner=None, age=0):
        data=dict(state='connected',enabled=True,session_saved=True,helper_available=True,
                  checked_at=self.now.isoformat(),username='test_owner')
        if owner:data.update(owner)
        self.path.write_text(json.dumps(dict(code='idle',checked_at=(self.now-timedelta(seconds=age)).isoformat(),telegram_owner=data)))

    def test_missing_stale_and_old_login_are_not_green(self):
        self.assertEqual(owner_connection(self.now)['code'],'unknown')
        self.report();self.assertEqual(owner_connection(self.now)['code'],'ready')
        self.report(age=121);self.assertEqual(owner_connection(self.now)['code'],'agent_stale')
        self.report(dict(checked_at=(self.now-timedelta(hours=2)).isoformat()))
        result=owner_connection(self.now)
        self.assertEqual(result['code'],'session_saved');self.assertEqual(result['tone'],'muted')

    def test_connection_problems_have_distinct_next_actions(self):
        cases=[({'enabled':False},'disabled'),({'state':'revoked'},'revoked'),
               ({'state':'not_connected','session_saved':False},'not_connected'),
               ({'helper_available':False},'helper_missing'),({'state':'connection_busy'},'connection_busy')]
        for data,code in cases:
            self.report(data);result=owner_connection(self.now)
            self.assertEqual(result['code'],code);self.assertTrue(result['next_action'])
            self.assertNotEqual(result['tone'],'success')

    def test_untrusted_fields_and_malformed_flags_do_not_leak_or_confirm(self):
        self.report(dict(session='SECRET',api_hash='SECRET',phone='SECRET',username='SECRET<script>'))
        self.assertNotIn('SECRET',json.dumps(owner_connection(self.now)))
        for data in ({'enabled':'true'},{'state':'SECRET'},{'state':['connected']},
                     {'checked_at':'not a date'},{'checked_at':(self.now+timedelta(days=1)).isoformat()}):
            self.report(data)
            result=owner_connection(self.now)
            self.assertNotEqual(result['code'],'ready');self.assertNotIn('SECRET',json.dumps(result))
