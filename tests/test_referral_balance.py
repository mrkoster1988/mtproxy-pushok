"""Награда приглашённому не выдаётся: фиксированный бонус получает пригласивший."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException
import test_postgres as fixture
import test_growth as growth_fixture
from app.growth import ReferralSettings
from app import wallet_ledger as ledger


class ReferralSettingsValidation(unittest.TestCase):
    def test_fixed_bonus_limits_and_mode(self):
        base = dict(referrals_enabled=True, reward_percent=10, reward_days=90, reward_mode='bonus')
        for value in (0, -1, 1000001, 1.5, True):
            with self.assertRaises(ValueError):
                ReferralSettings(**base, reward_bonus_kopecks=value)
        self.assertEqual(ReferralSettings(**base, reward_bonus_kopecks=12345).reward_bonus_kopecks, 12345)
        with self.assertRaises(ValueError):
            ReferralSettings(**{**base, 'reward_mode':'cash'})


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class ReferralBalanceIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request
    quote = growth_fixture.GrowthIntegration.quote
    payload = growth_fixture.GrowthIntegration.payload

    def setUp(self):
        growth_fixture.GrowthIntegration.setUp(self)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE growth_settings SET reward_mode='coupon',reward_bonus_kopecks=10000")
        self.settings()

    def settings(self, **changes):
        self.actor = {'id':self.admin,'role':'admin'}
        body = dict(referrals_enabled=True, reward_percent=20, reward_days=30,
                    reward_mode='bonus', reward_bonus_kopecks=12345)
        return self.request('PUT','/admin/growth/settings',json={**body, **changes})

    def bind(self):
        self.actor = {'id':self.owner,'role':'advertiser'}
        code = self.request('GET','/growth/me')['code']
        self.actor = {'id':self.friend,'role':'advertiser'}
        self.request('POST','/growth/referral',json={'code':code})
        return code

    def order(self, uid=None):
        self.actor = {'id':uid or self.friend,'role':'advertiser'}
        return UUID(self.request('POST','/orders',201,json=self.payload(self.quote()))['id'])

    def external(self, cid, *, test=False):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("""INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,
                provider_payment_id,sbp_test_mode,settled_at)
                VALUES(%s,'sbp','external_web',30000,'RUB','succeeded',%s,%s,now())""", (cid,uuid4().hex,test))

    def complete(self, cid):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='verified',placement_status='completed' WHERE id=%s",(cid,))

    def mine(self):
        self.actor = {'id':self.owner,'role':'advertiser'}
        return self.request('GET','/growth/me')

    def balance(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            return ledger.accounts(conn,self.owner)['BONUS']

    def test_snapshot_preserved_settings_changed_and_off(self):
        self.bind();cid=self.order();self.external(cid)
        self.settings(referrals_enabled=False,reward_mode='coupon',reward_bonus_kopecks=100)
        self.complete(cid);self.complete(cid)
        self.assertEqual(self.balance(),12345)
        mine=self.mine()
        self.assertEqual(mine['coupons'],[])
        self.assertEqual(mine['bonus_rewards'],dict(credited=12345,reversed=0))
        self.assertEqual(mine['invitations'][0]['reward_mode'],'bonus')
        self.assertEqual(mine['invitations'][0]['status'],'rewarded')
        self.assertEqual(mine['invitations'][0]['reward_bonus_kopecks'],12345)
        self.assertNotIn('referred_user_id',mine['invitations'][0])
        entries=self.request('GET','/wallet/entries')['entries']
        self.assertEqual(len(entries),1);self.assertEqual(entries[0]['kind'],'referral_reward')
        self.actor={'id':self.friend,'role':'advertiser'}
        self.assertEqual(self.request('GET','/wallet/entries')['entries'],[])

    def test_concurrent_first_completions_only_one_reward(self):
        self.bind();cids=[self.order(),self.order()]
        for cid in cids:self.external(cid)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(self.complete,cids))
        self.assertEqual(self.balance(),12345)
        self.assertEqual(len(self.mine()['invitations']),1)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_wallet_rewards WHERE referred_user_id=%s',(self.friend,)).fetchone()[0],1)

    def test_refund_after_bonus_spent_creates_debt_once_and_never_reissues(self):
        self.bind();cid=self.order();self.external(cid);self.complete(cid)
        own=self.order(self.owner)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            ledger.post(conn,self.owner,'RUB',30000,'manual_credit','fixture:'+str(uuid4()),'Тестовое пополнение')
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(own,))
            ledger.charge(conn,own,self.owner,'RUB')
        self.assertEqual(self.balance(),0)
        for _ in range(2):
            with psycopg.connect(self.dsn) as conn:
                conn.execute("UPDATE campaigns SET payment_status='refunded' WHERE id=%s",(cid,))
        self.assertEqual(self.balance(),-12345)
        self.assertEqual(self.mine()['bonus_rewards'],dict(credited=12345,reversed=12345))
        self.assertEqual(self.mine()['invitations'][0]['status'],'reversed')
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            entries=conn.execute("SELECT delta,kind,reversal_of FROM wallet_entries WHERE campaign_id=%s ORDER BY entry_number",(cid,)).fetchall()
            self.assertEqual([e['delta'] for e in entries],[12345,-12345])
            self.assertIsNotNone(entries[1]['reversal_of'])
        # Новая кампания друга не создаёт повторную награду после возврата.
        second=self.order();self.external(second);self.complete(second)
        self.assertEqual(self.balance(),-12345)
        another=self.order(self.owner)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(another,))
            with self.assertRaises(HTTPException) as err:ledger.charge(conn,another,self.owner,'RUB')
            self.assertEqual(err.exception.detail,'wallet_provider_refund_debt')
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(self.dsn) as conn:
                conn.execute("DELETE FROM wallet_entries WHERE campaign_id=%s AND kind='referral_reward'",(cid,))

    def test_no_reward_for_manual_free_test_or_full_bonus(self):
        self.bind()
        manual=self.order();self.complete(manual)
        free=self.order()
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE campaigns SET price_kopecks_snapshot=0 WHERE id=%s',(free,))
        self.external(free);self.complete(free)
        test=self.order();self.external(test,test=True);self.complete(test)
        bonus=self.order()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            ledger.post(conn,self.friend,'BONUS',30000,'manual_credit','fixture:'+str(uuid4()),'Тестовый подарок')
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(bonus,))
            ledger.charge(conn,bonus,self.friend,'RUB')
        self.complete(bonus)
        self.assertEqual(self.balance(),0)
        self.assertEqual(self.mine()['invitations'][0]['status'],'waiting')

    def test_wallet_cash_part_earns_fixed_bonus_and_refund_reverses(self):
        self.bind();cid=self.order()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            ledger.post(conn,self.friend,'RUB',20000,'manual_credit','fixture:'+str(uuid4()),'Тестовое пополнение')
            ledger.post(conn,self.friend,'BONUS',10000,'manual_credit','fixture:'+str(uuid4()),'Тестовый подарок')
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(cid,))
            ledger.charge(conn,cid,self.friend,'RUB')
        self.assertEqual(self.balance(),0);self.complete(cid)
        self.assertEqual(self.balance(),12345)
        for _ in range(2):
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:ledger.refund_campaign(conn,cid,self.admin,'Возврат по заявке')
        self.assertEqual(self.balance(),0)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(ledger.accounts(conn,self.friend),dict(RUB=20000,BONUS=10000,XTR=0))

    def test_sbp_requires_recorded_live_environment(self):
        self.bind()
        for test_mode in (True,None,False):
            cid=self.order()
            with psycopg.connect(self.dsn) as conn:
                conn.execute("""INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,
                    provider_payment_id,sbp_test_mode,settled_at)
                    VALUES(%s,'sbp','external_web',30000,'RUB','succeeded',%s,%s,now())""",(cid,uuid4().hex,test_mode))
            self.complete(cid)
            self.assertEqual(self.balance(),12345 if test_mode is False else 0)

    def test_legacy_coupon_survives_mode_change_and_migration_replay(self):
        self.settings(reward_mode='coupon');self.bind();cid=self.order();self.external(cid)
        self.settings(reward_mode='bonus');self.complete(cid)
        self.assertEqual(self.balance(),0);self.assertEqual(len(self.mine()['coupons']),1)
        root=Path(__file__).resolve().parents[1]
        with psycopg.connect(self.dsn) as conn:
            for _ in range(2):conn.execute((root/'database/037_referral_balance.sql').read_text())
        second=self.order();self.external(second);self.complete(second)
        self.assertEqual(self.balance(),0);self.assertEqual(len(self.mine()['coupons']),1)

    def test_snapshot_requires_new_invitation_role_and_no_self_cycle(self):
        code=self.bind();self.settings(reward_bonus_kopecks=777)
        self.actor={'id':self.friend,'role':'advertiser'}
        self.request('POST','/growth/referral',json={'code':code})
        self.request('PUT','/admin/growth/settings',403,json=dict(referrals_enabled=True,reward_percent=10,reward_days=30,reward_mode='bonus',reward_bonus_kopecks=777))
        friendcode=self.request('GET','/growth/me')['code']
        self.actor={'id':self.owner,'role':'advertiser'}
        self.request('POST','/growth/referral',409,json={'code':code})
        self.request('POST','/growth/referral',409,json={'code':friendcode})
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(conn.execute('SELECT reward_bonus_kopecks FROM growth_referrals WHERE referred_user_id=%s',(self.friend,)).fetchone()['reward_bonus_kopecks'],12345)
        cid=self.order();self.external(cid);self.complete(cid);self.assertEqual(self.balance(),12345)

    def test_migration_replay_keeps_bonus_journal_and_reconciliation(self):
        self.bind();cid=self.order();self.external(cid);self.complete(cid)
        root=Path(__file__).resolve().parents[1]
        with psycopg.connect(self.dsn) as conn:
            # Как установщик: 001 создаёт новую БД один раз, обновления идут с 002.
            for migration in sorted((root/'database').glob('*.sql')):
                if not migration.name.startswith('001_'):conn.execute(migration.read_text())
        self.complete(cid);self.assertEqual(self.balance(),12345)
        self.actor={'id':self.admin,'role':'admin'}
        data=self.request('GET','/admin/wallet/users/'+str(self.telegram))
        self.assertTrue(data['reconciled']);self.assertEqual(data['total'],1)
        self.request('POST','/admin/wallet/entries/'+data['entries'][0]['id']+'/reverse',409,json={'reason':'Нельзя отменить награду как ручную операцию'})
