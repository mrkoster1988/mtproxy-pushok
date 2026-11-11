"""СБП: авторизация и безопасные настройки без внешней сети."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from fastapi.testclient import TestClient
from app import main, sbp_checkout
from app.sbp_verification import validate_sbp_identity, verify_sbp_payment, SBPVerificationError


class SBPWebTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client=TestClient(main.app)
        self.cid=uuid4()
        self.base='/api/v1/campaigns/'+str(self.cid)+'/sbp'

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()

    def test_auth_precedes_database_and_provider(self):
        with patch('app.sbp_checkout.psycopg.connect') as db, patch('app.sbp_provider._request') as bank:
            for method,path in [('GET',self.base),('POST',self.base+'/checkout'),('POST',self.base+'/reconcile')]:
                self.assertEqual(self.client.request(method,path).status_code,401)
            db.assert_not_called();bank.assert_not_called()

    def test_disabled_checkout_never_creates_attempt_or_calls_bank(self):
        main.app.dependency_overrides[main.get_current_user]=lambda:{'id':uuid4(),'role':'advertiser'}
        with patch.dict(os.environ,{'PUSHOK_SBP_PAYMENTS_ENABLED':'0'}), patch('app.sbp_checkout.psycopg.connect') as db, patch('app.sbp_provider._request') as bank:
            response=self.client.post(self.base+'/checkout')
            self.assertEqual(response.status_code,409)
            db.assert_not_called();bank.assert_not_called()

    def test_foreign_campaign_is_hidden_before_provider_call(self):
        main.app.dependency_overrides[main.get_current_user]=lambda:{'id':uuid4(),'role':'advertiser'}
        conn=MagicMock();conn.__enter__.return_value.execute.return_value.fetchone.return_value=None
        with patch('app.sbp_checkout.psycopg.connect',return_value=conn), patch('app.sbp_provider._request') as bank:
            self.assertEqual(self.client.get(self.base).status_code,404)
            self.assertEqual(self.client.post(self.base+'/reconcile').status_code,404)
            bank.assert_not_called()

    def test_checkout_settings_fail_closed(self):
        with patch.dict(os.environ,{'PUSHOK_SBP_PAYMENTS_ENABLED':'1','YOOKASSA_SHOP_ID':'shop',
                'YOOKASSA_SECRET_KEY':'secret','PUSHOK_PUBLIC_PAYMENT_HOST':'ads.example.org','PUSHOK_SBP_TEST_MODE':'1'}):
            self.assertTrue(sbp_checkout.settings()['enabled'])
            self.assertTrue(sbp_checkout.settings()['test_mode'])
            for bad in ('ads.example.org:444','x/y','x..y','x\ny','https://x'):
                with patch.dict(os.environ,{'PUSHOK_PUBLIC_PAYMENT_HOST':bad}):
                    self.assertFalse(sbp_checkout.settings()['enabled'])

    def test_test_payment_cannot_match_live_snapshot(self):
        payment={'id':'id-1','status':'succeeded','paid':True,'test':True,'captured_at':'2026-11-11T12:00:00Z',
                 'payment_method':{'type':'sbp'},'metadata':{'campaign_id':str(self.cid)},
                 'amount':{'currency':'RUB','value':'300.55'}}
        args={'expected_payment_id':'id-1','expected_campaign_id':str(self.cid),'expected_kopecks':30055}
        self.assertEqual(validate_sbp_identity(payment,expected_test=True,**args),'id-1')
        with self.assertRaises(SBPVerificationError):validate_sbp_identity(payment,expected_test=False,**args)
        with self.assertRaises(SBPVerificationError):verify_sbp_payment({**payment,'refunded_amount':{'value':'1.00','currency':'RUB'}},**args)
