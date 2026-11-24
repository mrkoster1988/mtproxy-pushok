"""Standalone proof-format tests shared with Shop's verifier fixtures."""
import base64
import hashlib
import hmac
import json
import unittest
from uuid import UUID

from app.flow_shop_ticket import bridge_key, issue_ticket, DOMAIN

KEY='1a'*32

class FlowShopTicketTests(unittest.TestCase):
    def test_issue_is_short_lived_signed_and_distinct(self):
        subject=UUID('12345678-1234-4234-8234-123456789abc')
        token=issue_ticket(subject, 123456789, bridge_key(KEY), now=1700000000)
        typ, encoded, mac = token.split('.')
        self.assertEqual(typ,'psb1')
        raw=base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))
        self.assertEqual(mac, hmac.new(bytes.fromhex(KEY), DOMAIN+raw, hashlib.sha256).hexdigest())
        payload=json.loads(raw)
        self.assertEqual(payload['exp']-payload['iat'],60)
        self.assertEqual(payload['sub'],str(subject))
        self.assertEqual(payload['tg'],123456789)
        self.assertEqual((payload['aud'],payload['iss']),('pushok-shop','mtproxy'))
        self.assertRegex(payload['jti'],r'^[A-Za-z0-9_-]{32}$')
        self.assertNotEqual(token,issue_ticket(subject,123456789,bytes.fromhex(KEY),now=1700000000))

    def test_key_and_claim_validation(self):
        for raw in ('', 'A'*64, '12', 'g'*64):
            with self.assertRaises(ValueError):bridge_key(raw)
        subject='12345678-1234-4234-8234-123456789abc'
        for tg in (0, -1, True, 2**52, '123'):
            with self.assertRaises(ValueError):issue_ticket(subject,tg,bytes.fromhex(KEY))
        with self.assertRaises(ValueError):issue_ticket(subject,123,None)
        with self.assertRaises(ValueError):issue_ticket('secret-user-id',123,bytes.fromhex(KEY))

if __name__=='__main__':
    unittest.main()
