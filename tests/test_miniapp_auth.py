import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from app import main
from app.miniapp_auth import validate_init_data
from fastapi.testclient import TestClient


def signed_data(token='fixture-token', **changes):
    data = {'auth_date': str(int(time.time())), 'query_id': 'fixture-query',
            'user': json.dumps({'id': 12345, 'first_name': 'Пушок'}), 'signature': 'signed-field'}
    data.update(changes)
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    data['hash'] = hmac.new(secret, '\n'.join(f'{k}={data[k]}' for k in sorted(data)).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class MiniAppAuthTests(unittest.TestCase):
    def test_client_entry_is_separate_and_config_has_no_secrets(self):
        with TestClient(main.app) as client:
            page = client.get('/miniapp')
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['cache-control'], 'no-store')
            self.assertIn('data-miniapp="true"', page.text)
            self.assertLess(page.text.index('/assets/miniapp.js'), page.text.index('/assets/app.js'))
            self.assertNotIn('/assets/miniapp.js', client.get('/').text)
            with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '0'}):
                self.assertEqual(client.get('/api/v1/miniapp/config').json(), {'enabled': False})
            with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1'}):
                self.assertEqual(client.get('/api/v1/miniapp/config').json(), {'enabled': True})

    def test_correct_signature_and_reordered_replay_identity(self):
        raw = signed_data()
        result = validate_init_data(raw, 'fixture-token')
        self.assertEqual(result['id'], 12345)
        self.assertEqual(result['hash'], validate_init_data('&'.join(reversed(raw.split('&'))), 'fixture-token')['hash'])

    def test_tampered_expired_duplicate_and_wrong_bot_are_rejected(self):
        raw = signed_data()
        for value, token in [(raw, 'other-bot'), (raw+'&user=forged', 'fixture-token'),
                (signed_data(auth_date=str(int(time.time())-301)), 'fixture-token'),
                (signed_data(auth_date=str(int(time.time())+100)), 'fixture-token'),
                (signed_data(user='{"id":true,"first_name":"x"}'), 'fixture-token'),
                (raw.replace('fixture-query', 'forged'), 'fixture-token')]:
            with self.assertRaises(main.HTTPException):
                validate_init_data(value, token)

    def test_gate_and_invalid_signature_precede_database(self):
        with TestClient(main.app) as client, patch('app.main.psycopg.connect') as db:
            with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '0'}):
                self.assertEqual(client.post('/api/v1/auth/telegram/miniapp', json={'init_data': signed_data()}).status_code, 409)
            with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1'}), patch.object(main, 'TELEGRAM_BOT_TOKEN', 'other-token'):
                self.assertEqual(client.post('/api/v1/auth/telegram/miniapp', json={'init_data': signed_data()}).status_code, 401)
            db.assert_not_called()
