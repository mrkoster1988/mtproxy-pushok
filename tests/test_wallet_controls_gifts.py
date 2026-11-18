"""Выключатель и подарки: реальные транзакции, гонки и сохранение обязательств."""
import unittest
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4
from fastapi import HTTPException
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import wallet_gifts as gifts, wallet_topups as topups, wallet_worker, wallet_ledger as ledger
from app.wallet import WalletSettings, Topup


class GiftValidation(unittest.TestCase):
    def test_released_migration_037_checksum_is_preserved(self):
        # Установщик v1.17 записал этот digest; изменения существующего файла блокируют upgrade.
        old=Path(__file__).resolve().parents[1]/'database/037_referral_balance.sql'
        self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),'f09133ee15371a329714c47020c5ae8a98002d81c71d1279bb2dda54647b595e')

    def test_strict_limits_timezone_and_code(self):
        data=dict(request_id=uuid4(),name='Подарок',amount=100,max_uses=1,expires_at=datetime.now(timezone.utc)+timedelta(days=1))
        for amount in (True,0,-1,1.5,1000001):
            with self.assertRaises(ValueError):gifts.GiftCreate(**{**data,'amount':amount})
        for maximum in (True,0,1.2,10001):
            with self.assertRaises(ValueError):gifts.GiftCreate(**{**data,'max_uses':maximum})
        with self.assertRaises(ValueError):gifts.GiftCreate(**{**data,'expires_at':datetime.now()})
        with self.assertRaises(ValueError):WalletSettings(enabled='false')
        self.assertEqual(gifts.GiftRedeem(code=' gift-'+'a'*24+' ').code,'GIFT-'+'A'*24)
        with self.assertRaises(ValueError):gifts.GiftRedeem(code='x'*29)


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class WalletControlsGiftsIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    campaign=wallet_fixture.WalletIntegration.campaign
    credit=wallet_fixture.WalletIntegration.credit
    balances=wallet_fixture.WalletIntegration.balances
    charge=wallet_fixture.WalletIntegration.charge
    star_topup=wallet_fixture.WalletIntegration.star_topup

    def setUp(self):
        wallet_fixture.WalletIntegration.setUp(self)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=true')

    def switch(self,enabled):
        self.actor={'id':self.admin,'role':'admin'}
        result=self.request('PUT','/admin/wallet/settings',json={'enabled':enabled})
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'web'}
        return result

    def test_bonus_spending_policy_for_manual_and_automatic_payments(self):
        self.credit('BONUS',30000)
        self.credit('RUB',30000)
        paid=self.campaign()
        self.assertEqual(self.charge(paid)['bonus_amount'],30000)
        self.credit('BONUS',30000)
        self.actor={'id':self.admin,'role':'admin'}
        endpoint='/admin/wallet/settings'
        self.request('PUT',endpoint,json={'enabled':True,'bonus_spending_enabled':False})
        self.addCleanup(lambda: self.request('PUT',endpoint,json={'enabled':True,'bonus_spending_enabled':True}))
        self.addCleanup(setattr,self,'actor',{'id':self.admin,'role':'admin'})
        self.assertFalse(self.request('PUT',endpoint,json={'enabled':True})['bonus_spending_enabled'])
        self.assertEqual(self.charge(paid)['bonus_amount'],30000)
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'web'}
        cid=self.campaign()
        self.assertFalse(self.request('GET',f'/wallet/campaigns/{cid}')['bonus_spending_enabled'])
        with self.assertRaises(HTTPException) as error:
            self.charge(cid,use_bonus=True)
        self.assertEqual(error.exception.detail,'wallet_bonus_disabled')
        self.request('PUT','/wallet/preferences',json=dict(auto_pay=True,currency='RUB',per_campaign_limit=30000,use_bonus=True))
        self.assertEqual(self.charge(cid,automatic=True)['bonus_amount'],0)
        self.assertEqual(self.balances()['BONUS'],30000)
        with topups.connect(self.dsn) as conn:
            ledger.refund_campaign(conn,paid,self.admin,'Отмена размещения')
        self.assertEqual(self.balances()['BONUS'],60000)

    def gift(self,**kw):
        data=gifts.GiftCreate(request_id=uuid4(),name='Подарок другу',amount=12345,max_uses=kw.pop('max_uses',1),
                              expires_at=datetime.now(timezone.utc)+timedelta(days=1),**kw)
        with topups.connect(self.dsn) as conn:return gifts.create(conn,self.admin,data)

    def redeem(self,code,uid=None):
        with topups.connect(self.dsn) as conn:return gifts.redeem(conn,uid or self.owner,code)

    def test_disabled_blocks_new_actions_preserves_data_and_direct_checkout(self):
        self.credit();cid=self.campaign();gift=self.gift()
        self.request('PUT','/wallet/preferences',json=dict(auto_pay=True,currency='RUB',per_campaign_limit=30000))
        self.switch(False)
        own=self.request('GET','/wallet');self.assertFalse(own['enabled']);self.assertFalse(own['preferences']['auto_pay']);self.assertEqual(own['balances']['RUB'],30000)
        self.assertFalse(self.request('GET','/wallet/campaigns/'+str(cid))['payable'])
        self.request('POST','/wallet/campaigns/'+str(cid)+'/pay',409,json=dict(currency='RUB',expected_amount=30000))
        self.request('PUT','/wallet/preferences',409,json=dict(auto_pay=True,currency='XTR',per_campaign_limit=100))
        self.request('POST','/wallet/gifts/redeem',409,json={'code':gift['code']})
        with patch('app.wallet_topups.stars_invoices_enabled',return_value=True),patch('app.wallet_topups.stars_checkout.credentials',return_value=('token',12345)),patch('app.wallet_topups.stars_checkout.create_invoice_link') as provider:
            self.request('POST','/wallet/topups',409,json=dict(request_id=str(uuid4()),currency='XTR',amount=100));provider.assert_not_called()
        wallet_worker.service_once(self.dsn);self.assertEqual(self.balances()['RUB'],30000)
        # Прямая оплата не использует кошелёк, её возможность не выключена.
        with topups.connect(self.dsn) as conn:self.assertTrue(ledger.payable(conn,ledger.campaign(conn,cid,self.owner)))
        self.switch(True);self.assertFalse(self.request('GET','/wallet')['preferences']['auto_pay'])
        self.request('POST','/wallet/campaigns/'+str(cid)+'/pay',json=dict(currency='RUB',expected_amount=30000))
        self.assertEqual(self.balances()['RUB'],0)

    def test_existing_stars_receipt_and_refund_while_disabled(self):
        row,pre,receipt=self.star_topup();self.switch(False)
        self.assertTrue(topups.precheckout(self.dsn,pre)['ok'])
        topups.settle_stars(self.dsn,receipt);topups.settle_stars(self.dsn,receipt)
        self.assertEqual(self.balances()['XTR'],300)
        topups.refund_stars(self.dsn,receipt);self.assertEqual(self.balances()['XTR'],0)
        self.credit();self.switch(True);cid=self.campaign();self.charge(cid);self.switch(False)
        with topups.connect(self.dsn) as conn:ledger.refund_campaign(conn,cid,self.admin,'Отмена размещения')
        self.assertEqual(self.balances()['RUB'],30000)

    def test_resume_blocked_and_manual_accounting_allowed(self):
        with topups.connect(self.dsn) as conn:
            row=conn.execute("INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode) VALUES(%s,%s,'telegram_stars','XTR',100,'12345',false) RETURNING id",(self.owner,uuid4())).fetchone()
        self.switch(False)
        self.request('POST','/wallet/topups/'+str(row['id'])+'/resume',409)
        self.actor={'id':self.admin,'role':'admin'}
        self.request('POST','/admin/wallet/users/'+str(self.tg)+'/adjust',201,json=dict(request_id=str(uuid4()),currency='RUB',amount=100,direction='credit',reason='Сверка платежа'))
        self.assertEqual(self.balances()['RUB'],100)

    def test_gift_retry_and_idempotent_create_roles(self):
        body=dict(request_id=str(uuid4()),name='Подарочный бонус',amount=12345,max_uses=2,expires_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
        self.request('GET','/admin/wallet/settings',403)
        self.request('PUT','/admin/wallet/settings',403,json={'enabled':False})
        self.request('POST','/admin/wallet/gifts',403,json=body)
        self.request('GET','/admin/wallet/gifts',403)
        self.actor={'id':self.admin,'role':'admin'}
        first=self.request('POST','/admin/wallet/gifts',201,json=body)
        second=self.request('POST','/admin/wallet/gifts',201,json=body)
        self.assertEqual(first['code'],second['code'])
        self.request('POST','/admin/wallet/gifts',409,json={**body,'amount':1})
        self.actor={'id':self.owner,'role':'advertiser'}
        self.request('PUT','/admin/wallet/gifts/'+first['id'],403,json={'active':False})
        for n in range(2):
            result=self.request('POST','/wallet/gifts/redeem',json={'code':first['code'].lower()})
            self.assertEqual(result['already_redeemed'],bool(n))
        self.assertEqual(self.balances()['BONUS'],12345)
        journal=self.request('GET','/wallet/entries');self.assertEqual(journal['total'],1);self.assertEqual(journal['entries'][0]['kind'],'gift_credit')
        self.actor={'id':self.other,'role':'advertiser'};self.assertEqual(self.request('GET','/wallet/entries')['total'],0)
        self.actor={'id':self.admin,'role':'admin'}
        self.assertEqual(self.request('GET','/admin/wallet/gifts')['items'][0]['used'],1)

    def test_last_use_race_two_users_and_same_user_retry(self):
        gift=self.gift()
        def attempt(uid):
            try:return self.redeem(gift['code'],uid)['entry_id']
            except HTTPException as exc:return exc.detail
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[self.owner,self.other]))
        self.assertEqual(results.count('wallet_gift_limit_reached'),1)
        with topups.connect(self.dsn) as conn:
            winner=conn.execute('SELECT user_id FROM wallet_gift_redemptions WHERE gift_id=%s',(gift['id'],)).fetchone()['user_id']
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.redeem(gift['code'],winner),range(2)))
        self.assertTrue(all(r['already_redeemed'] for r in results))
        with topups.connect(self.dsn) as conn:self.assertEqual(conn.execute("SELECT count(*) AS n FROM wallet_entries WHERE kind='gift_credit' AND user_id=%s",(winner,)).fetchone()['n'],1)

    def test_deactivate_expiry_and_existing_redemption_preserved(self):
        gift=self.gift(max_uses=2);self.redeem(gift['code'])
        self.actor={'id':self.admin,'role':'admin'}
        self.request('PUT','/admin/wallet/gifts/'+str(gift['id']),json={'active':False})
        self.assertTrue(self.redeem(gift['code'])['already_redeemed'])
        with self.assertRaises(HTTPException):self.redeem(gift['code'],self.other)
        self.request('PUT','/admin/wallet/gifts/'+str(gift['id']),json={'active':True})
        with topups.connect(self.dsn) as conn:conn.execute("UPDATE wallet_gifts SET expires_at=now()-interval '1 second' WHERE id=%s",(gift['id'],))
        with self.assertRaises(HTTPException):self.redeem(gift['code'],self.other)
        self.assertEqual(self.balances()['BONUS'],12345)

    def test_rollback_no_spent_activation_and_replay_migrations(self):
        gift=self.gift()
        with self.assertRaises(RuntimeError):
            with topups.connect(self.dsn) as conn:
                gifts.redeem(conn,self.owner,gift['code']);raise RuntimeError('Откат всей транзакции')
        self.assertEqual(self.balances()['BONUS'],0)
        self.redeem(gift['code'])
        root=Path(__file__).resolve().parents[1]
        with topups.connect(self.dsn) as conn:
            # Старые миграции установщик пропускает по журналу; новая допускает повтор.
            for filename in ('038_wallet_controls_gifts.sql','038_wallet_controls_gifts.sql'):
                conn.execute((root/'database'/filename).read_text())
        self.assertEqual(self.balances()['BONUS'],12345)
        self.assertTrue(self.redeem(gift['code'])['already_redeemed'])

    def test_stars_internal_payment_preserves_personal_balance_unknown(self):
        self.credit('XTR',300);cid=self.campaign(surface='telegram',xtr=151)
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'telegram'}
        self.assertTrue(self.request('GET','/wallet/campaigns/'+str(cid))['stars_only'])
        for _ in range(2):self.request('POST','/wallet/campaigns/'+str(cid)+'/pay',json=dict(currency='XTR',expected_amount=151))
        self.assertEqual(self.balances()['XTR'],149)
        self.assertNotIn('telegram_stars_balance',self.request('GET','/wallet'))

    def test_exhausted_gifts_archive_keeps_partial_codes_and_history(self):
        single=self.gift();partial=self.gift(max_uses=2)
        self.redeem(single['code']);self.redeem(partial['code'])
        self.actor={'id':self.admin,'role':'admin'}
        active=self.request('GET','/admin/wallet/gifts?archived=false')
        archived=self.request('GET','/admin/wallet/gifts?archived=true')
        self.assertIn(str(partial['id']),[g['id'] for g in active['items']])
        self.assertNotIn(str(single['id']),[g['id'] for g in active['items']])
        self.assertIn(str(single['id']),[g['id'] for g in archived['items']])
        report=self.request('GET','/admin/wallet/gifts/'+str(single['id'])+'/redemptions')
        self.assertEqual(report['total'],1)
