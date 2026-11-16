import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"api"))
from app import sbp_connection as connection, control, sbp_provider, sbp_checkout

class SBPConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PUSHOK_CONTROL_DIR':self.temp.name,'YOOKASSA_SHOP_ID':'','YOOKASSA_SECRET_KEY':''})
        self.env.start()
        self.allowed=True
        def admin():
            if not self.allowed:raise HTTPException(403,'admin_required')
            return {'id':'00000000-0000-0000-0000-000000000001'}
        app=FastAPI();app.include_router(connection.router_for(lambda:'dsn',admin));self.client=TestClient(app)
        self.data=dict(shop='12345',secret='test_secret_abcdefgh',host='ads.example.org',test_mode=True)
        self.db=MagicMock();self.db.__enter__.return_value=self.db
        self.db.execute.return_value.fetchone.return_value=(False,)
        self.connect=patch('psycopg.connect',return_value=self.db);self.connect.start()
        self.probe=patch.object(sbp_provider,'_request',return_value=dict(account_id='12345',test=True,payment_methods=['sbp'],status='enabled'))
        self.request=self.probe.start()
    def tearDown(self):
        self.probe.stop();self.connect.stop();self.env.stop();self.temp.cleanup()
    def test_save_checks_shop_and_never_exposes_secret(self):
        result=self.client.put('/api/v1/admin/sbp/connection',json=self.data)
        self.assertEqual(result.status_code,200,result.text)
        self.assertNotIn(self.data['secret'],result.text)
        self.assertNotIn(self.data['secret'],self.client.get('/api/v1/admin/sbp/connection').text)
        self.assertEqual(self.request.call_args.args,('GET','https://api.yookassa.ru/v3/me'))
        self.assertEqual(sbp_provider._credentials(),('12345',self.data['secret']))
        self.assertEqual(sbp_checkout.settings()['host'],'ads.example.org')
        self.assertEqual(Path(self.temp.name,'sbp-private.json').stat().st_mode&0o777,0o600)
        self.assertFalse(control.flag('sbp',False))
    def test_failed_verification_preserves_previous_configuration(self):
        self.request.side_effect=sbp_provider.SBPProviderError('network')
        response=self.client.put('/api/v1/admin/sbp/connection',json=self.data)
        self.assertEqual(response.status_code,502)
        self.assertIsNone(control.read('sbp-private.json'))
    def test_wrong_shop_or_mode_or_missing_sbp_is_rejected(self):
        for changed in [dict(account_id='54321'),dict(test=False),dict(payment_methods=['bank_card']),dict(status='disabled')]:
            self.request.return_value=dict(account_id='12345',test=True,payment_methods=['sbp'],status='enabled')|changed
            self.assertEqual(self.client.put('/api/v1/admin/sbp/connection',json=self.data).status_code,409)
        self.assertIsNone(control.read('sbp-private.json'))
    def test_only_admin_can_read_or_write(self):
        self.allowed=False
        self.assertEqual(self.client.get('/api/v1/admin/sbp/connection').status_code,403)
        self.assertEqual(self.client.put('/api/v1/admin/sbp/connection',json=self.data).status_code,403)
        self.request.assert_not_called()
    def test_existing_payments_block_configuration_change(self):
        self.db.execute.return_value.fetchone.return_value=(True,)
        self.assertEqual(self.client.put('/api/v1/admin/sbp/connection',json=self.data).status_code,409)
        self.request.assert_not_called()
    def test_blank_key_keeps_saved_key_and_rotation_is_checked(self):
        self.client.put('/api/v1/admin/sbp/connection',json=self.data)
        data={k:v for k,v in self.data.items() if k!='secret'}
        self.assertEqual(self.client.put('/api/v1/admin/sbp/connection',json=data).status_code,200)
        self.assertEqual(connection.current()['secret'],self.data['secret'])
    def test_host_and_key_reject_untrusted_input(self):
        for changed in [dict(host='https://evil.example/path'),dict(secret='bad\nkey'),dict(host='a..b')]:
            self.assertEqual(self.client.put('/api/v1/admin/sbp/connection',json=self.data|changed).status_code,422)
        self.request.assert_not_called()

    def test_invoice_creation_waits_for_configuration_lock(self):
        import threading
        started=threading.Event();entered=threading.Event()
        @connection.serialized_creation
        def invoice():
            entered.set()
        def worker():
            started.set()
            invoice()
        with control.locked():
            thread=threading.Thread(target=worker,daemon=True);thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(entered.wait(0.05))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(entered.is_set())

    def test_private_configuration_is_in_backup_allowlist(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import cabinet_backup
        self.assertEqual(cabinet_backup.CONFIG['.control/sbp-private.json'],'sbp-private.json')
        self.assertIn('sbp-private.json',cabinet_backup.ALLOWED)

    def test_corrupt_saved_configuration_never_falls_back_to_environment(self):
        Path(self.temp.name,'sbp-private.json').write_text('broken')
        with patch.dict(os.environ,{'YOOKASSA_SHOP_ID':'999','YOOKASSA_SECRET_KEY':'secret_from_env'}):
            self.assertEqual(connection.current(),{})

    def test_wrong_saved_types_fail_closed(self):
        for values in (dict(host=None),dict(secret=[]),dict(test_mode='false')):
            control.write('sbp-private.json',{**self.data,**values})
            self.assertEqual(connection.current(),{})
            self.assertFalse(sbp_checkout.settings()['configured'])
