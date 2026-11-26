import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'api'))
from app import wallet_connections, wallet_acquirers


class WalletConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'PUSHOK_CONTROL_DIR': directory}))
        self.conn = MagicMock()
        self.conn.execute.return_value.fetchone.return_value = None
        self.enterContext(patch('psycopg.connect')).return_value.__enter__.return_value = self.conn
        app = FastAPI()
        app.include_router(wallet_connections.router_for(lambda: 'test', lambda: {'id': 'owner'}))
        self.client = TestClient(app)
        self.path = '/api/v1/admin/wallet/connections'
        self.form = dict(account='shop-id', secret='merchant-private-test', test_mode=True, enabled=True)

    def test_saved_credentials_apply_to_checkout_and_never_appear_in_api(self):
        response = self.client.put(self.path+'/platega', json=self.form)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['secret_saved'])
        self.assertNotIn(self.form['secret'], self.client.get(self.path).text)
        self.assertNotIn(self.form['secret'], response.text)
        self.assertEqual(wallet_acquirers.settings('platega')['credentials']['X-Secret'], self.form['secret'])
        body = {k: v for k, v in self.form.items() if k != 'secret'}
        response = self.client.put(self.path+'/platega', json={**body, 'enabled': False})
        self.assertEqual(response.status_code, 200, response.text)
        saved = wallet_acquirers.settings('platega')
        self.assertTrue(saved['configured'])
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['credentials']['X-Secret'], self.form['secret'])

    def test_open_invoice_prevents_change_of_merchant_mode(self):
        self.client.put(self.path+'/platega', json=self.form).raise_for_status()
        self.conn.execute.return_value.fetchone.return_value = (1,)
        self.assertEqual(self.client.put(self.path+'/platega', json={**self.form,'test_mode':False}).status_code, 409)
        self.assertTrue(wallet_acquirers.settings('platega')['test_mode'])

    def test_invalid_antilopay_key_preserves_previous_configuration(self):
        response = self.client.put(self.path+'/antilopay', json={**self.form, 'project':'project'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(self.form['secret'], response.text)
        self.assertFalse(wallet_acquirers.settings('antilopay')['enabled'])
