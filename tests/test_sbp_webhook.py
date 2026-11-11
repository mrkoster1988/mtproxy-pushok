"""Webhook не подтверждает платёж, ограничивает тело и не раскрывает счёт."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from app import main, sbp_webhook

PATH = '/api/v1/payments/sbp/webhook'
PAYLOAD = {'type': 'notification', 'event': 'payment.succeeded',
           'object': {'id': 'bank-123', 'status': 'succeeded', 'paid': True}}


class SBPWebhookTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.env = patch.dict(os.environ, {'PUSHOK_SBP_WEBHOOK_ENABLED': '1',
                              'PUSHOK_SBP_RECONCILIATION_ENABLED': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.client.close)

    def test_disabled_and_stopped_worker_never_touch_database(self):
        with patch.object(sbp_webhook, 'wake_payment') as wake:
            with patch.dict(os.environ, {'PUSHOK_SBP_WEBHOOK_ENABLED': '0'}):
                self.assertEqual(self.client.post(PATH, json=PAYLOAD).status_code, 404)
            with patch.dict(os.environ, {'PUSHOK_SBP_RECONCILIATION_ENABLED': '0'}):
                self.assertEqual(self.client.post(PATH, json=PAYLOAD).status_code, 503)
            wake.assert_not_called()

    def test_untrusted_payload_passes_only_payment_id_without_provider_call(self):
        with patch.object(sbp_webhook, 'wake_payment') as wake, \
                patch('app.sbp_provider._request') as bank:
            response = self.client.post(PATH, json={**PAYLOAD, 'metadata': {'campaign_id': 'foreign'},
                                                   'amount': {'value': '0.01'}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            wake.assert_called_once_with(os.environ['DATABASE_URL'], 'bank-123')
            bank.assert_not_called()

    def test_malformed_and_oversized_payloads_rejected_before_database(self):
        invalid = [[], None, {}, {**PAYLOAD, 'type': 'payment'},
                   {**PAYLOAD, 'event': []}, {**PAYLOAD, 'object': []},
                   {**PAYLOAD, 'object': {'id': '../foreign'}},
                   {**PAYLOAD, 'object': {'id': 'x' * 81}}]
        with patch.object(sbp_webhook, 'wake_payment') as wake:
            for body in invalid:
                response = self.client.post(PATH, content=json.dumps(body),
                                            headers={'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.client.post(PATH, content=b'{', headers={'Content-Type': 'application/json'}).status_code, 400)
            # Проверяется реальный размер, даже если клиент заявил ложный Content-Length.
            response = self.client.post(PATH, content=b' ' * 16385,
                headers={'Content-Type': 'application/json', 'Content-Length': '2'})
            self.assertEqual(response.status_code, 413)
            self.assertEqual(self.client.post(PATH, content=b'{}').status_code, 415)
            wake.assert_not_called()

    def test_database_failure_is_not_acknowledged(self):
        with patch.object(sbp_webhook, 'wake_payment', side_effect=psycopg.OperationalError('secret')):
            response = self.client.post(PATH, json=PAYLOAD)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('secret', response.text)

    def test_other_events_acknowledged_without_mutation(self):
        with patch.object(sbp_webhook, 'wake_payment') as wake:
            self.assertEqual(self.client.post(PATH, json={**PAYLOAD, 'event': 'refund.succeeded'}).status_code, 200)
            wake.assert_not_called()
