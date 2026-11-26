import base64
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import test_postgres as fixture
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from app import wallet_acquirers as providers, wallet_topups, wallet_worker
from app.wallet import Topup
from app.sbp_verification import SBPVerificationError


def order(provider):
    return dict(id=uuid4(), provider=provider, amount=2350, return_url='https://example.org/wallet',
                created_at=datetime.now(timezone.utc), receipt_email='buyer@example.org',
                provider_payment_id=str(uuid4()))


class ProviderContracts(unittest.TestCase):
    def test_platega_idempotent_fixed_link_and_verified_transaction(self):
        row = order('platega')
        tid, link, tx = str(row['id']), row['provider_payment_id'], str(uuid4())
        cfg = dict(credentials={'X-MerchantId': 'merchant', 'X-Secret': 'secret'})
        with patch.object(providers,'request',return_value={'id':link,'url':'https://pay.example.org/i'}) as api:
            providers.create(row,cfg)
            first = api.call_args
            providers.create(row,cfg)
            self.assertEqual(first, api.call_args)
            url, headers, content = first.args
            self.assertTrue(url.endswith('/v2/payment-links'))
            data = json.loads(content)
            self.assertEqual((headers['Idempotency-Key'],data['orderId'],data['payload']), (tid,tid,tid))
            self.assertEqual((data['minAmount'], data['maxAmount'], data['usageMode']), ('23.50','23.50',1))
        link_data = dict(id=link, orderId=tid, usageMode=1, consumedTransactionId=tx)
        payment = dict(id=tx, payload=tid, status='CONFIRMED', paymentDetails={'currency':'RUB','amount':'23.50'})
        with patch.object(providers,'request',side_effect=[link_data,payment]):
            self.assertEqual(providers.inspect_payment(row,cfg), ('paid',0))
        for change in ({'id':str(uuid4())}, {'payload':'wrong'}, {'paymentDetails':{'currency':'USD','amount':'23.50'}},
                       {'paymentDetails':{'currency':'RUB','amount':'23.501'}}):
            with patch.object(providers,'request', side_effect=[link_data, {**payment,**change}]), self.assertRaises(SBPVerificationError):
                providers.inspect_payment(row,cfg)
        with patch.object(providers,'request',return_value={**link_data,'consumedTransactionId':None,'active':True}):
            self.assertEqual(providers.inspect_payment(row,cfg), ('pending',0))

    def test_antilopay_signs_exact_bytes_and_recovers_duplicate(self):
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        encoded = base64.b64encode(key.private_bytes(serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption())).decode()
        with patch.dict(os.environ,{'ANTILOPAY_PRIVATE_KEY_BASE64':encoded,'ANTILOPAY_PROJECT_ID':'project',
                                    'ANTILOPAY_SECRET_ID':'merchant','ANTILOPAY_ENABLED':'1'}):
            cfg=providers.settings('antilopay')
        self.assertTrue(cfg['enabled'])
        row=order('antilopay')
        original=dict(code=0,payment_id=row['provider_payment_id'],order_id=str(row['id']),
                      payment_url='https://gate.antilopay.com/i',currency='RUB',original_amount=23.5)
        with patch.object(providers,'request',side_effect=[{'code':8},{'code':5},original]) as api:
            self.assertEqual(providers.create(row,cfg),(original['payment_id'],original['payment_url']))
            url,headers,body=api.call_args_list[1].args
            self.assertTrue(url.endswith('/v1/payment/create'))
            key.public_key().verify(base64.b64decode(headers['X-Apay-Sign']),body,padding.PKCS1v15(),hashes.SHA256())
            self.assertEqual(json.loads(body)['prefer_methods'],['SBP'])
            self.assertEqual(json.loads(body)['customer'],{'email':row['receipt_email']})
        refund={'refund_id':'r1','status':'COMPLETE','amount':'5.15'}
        with patch.object(providers,'ant_request',return_value={**original,'status':'SUCCESS',
                          'amount':21,'refunds':[refund,refund]}):
            self.assertEqual(providers.inspect_payment(row,cfg),('paid',515))


@unittest.skipUnless(fixture._approved_test_postgres(), 'Тестовый PostgreSQL')
class WalletProviderIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)

    def setUp(self):
        self.uid=uuid4()
        self.actor={'id':self.uid,'login_surface':'web'}
        with wallet_topups.connect(self.dsn) as conn:
            conn.execute("INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,'advertiser')",
                         (self.uid,int(self.uid.hex[:12],16)))
        self.cfg=dict(shop='m1',host='pay.example.org',configured=True,enabled=True,test_mode=False)
        mocked=patch.object(providers,'settings',return_value=self.cfg)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_limits_access_and_duplicate_submission(self):
        for provider, minimum in providers.MINIMUM.items():
            for amount in (minimum-1,1000001):
                data=Topup(currency='RUB',provider=provider,amount=amount,request_id=uuid4())
                with self.assertRaises(HTTPException) as raised:
                    wallet_topups.create(self.dsn,self.actor,data)
                self.assertEqual(raised.exception.detail,'wallet_method_amount')
        data=Topup(currency='RUB',provider='antilopay',amount=2000,request_id=uuid4())
        with self.assertRaises(HTTPException) as raised:
            wallet_topups.create(self.dsn,self.actor,data)
        self.assertEqual(raised.exception.detail,'wallet_receipt_email')
        data=data.model_copy(update={'receipt_email':'test@example.org'})
        with self.assertRaises(HTTPException):
            wallet_topups.create(self.dsn,{**self.actor,'login_surface':'telegram'},data)
        with patch.object(providers,'create',return_value=('apay1','https://example.org/pay')) as api:
            result=wallet_topups.create(self.dsn,self.actor,data)
            repeat=wallet_topups.create(self.dsn,self.actor,data)
            self.assertEqual(repeat['id'],result['id'])
            api.assert_called_once()
        with wallet_topups.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute('SELECT receipt_email FROM wallet_topups WHERE id=%s',(result['id'],)).fetchone()['receipt_email'])

    def test_definite_rejection_releases_the_wallet_and_test_money_is_not_credited(self):
        data=Topup(currency='RUB',provider='platega',amount=1000,request_id=uuid4())
        with patch.object(providers,'create',side_effect=providers.InvoiceRejected()):
            self.assertEqual(wallet_topups.create(self.dsn,self.actor,data)['status'],'failed')
        self.cfg['test_mode']=True
        with patch.object(providers,'create',return_value=(str(uuid4()),'https://example.org/pay')):
            tid=wallet_topups.create(self.dsn,self.actor,data.model_copy(update={'request_id':uuid4()}))['id']
        with patch.object(providers,'inspect_payment',return_value=('paid',0)):
            self.assertEqual(wallet_topups.verify(self.dsn,tid,self.uid)['status'],'succeeded')
        with wallet_topups.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM wallet_entries WHERE topup_id=%s',(tid,)).fetchone())

    def test_concurrent_reconciliation_refund_and_disabled_method(self):
        for provider in providers.MINIMUM:
            data=Topup(currency='RUB',provider=provider,amount=1000000,request_id=uuid4(),receipt_email='test@example.org')
            self.cfg['enabled']=True
            with patch.object(providers,'create',return_value=(str(uuid4()),'https://example.org/pay')):
                tid=wallet_topups.create(self.dsn,self.actor,data)['id']
            self.cfg['enabled']=False
            with patch.object(providers,'inspect_payment',return_value=('paid',0)):
                wallet_worker.service_once(self.dsn)
                with ThreadPoolExecutor(2) as pool:
                    self.assertEqual([s['status'] for s in pool.map(lambda _:wallet_topups.verify(self.dsn,tid,self.uid),range(2))],['succeeded']*2)
            with patch.object(providers,'inspect_payment',return_value=('paid',1000000)):
                result=wallet_topups.verify(self.dsn,tid,self.uid)
                self.assertEqual(result['status'],'refunded')
            with wallet_topups.connect(self.dsn) as conn:
                rows=conn.execute('SELECT delta FROM wallet_entries WHERE topup_id=%s ORDER BY entry_number',(tid,)).fetchall()
                self.assertEqual([r['delta'] for r in rows],[1000000,-1000000])
