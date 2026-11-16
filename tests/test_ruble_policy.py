"""Ruble-only promotions and legacy Stars accounting compatibility."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
import test_postgres as fixture
from fastapi import HTTPException
from app import growth, wallet_topups

class RublePolicy(unittest.TestCase):
    def test_no_new_stars_topup_or_provider_call(self):
        with patch.object(wallet_topups,'connect') as db,patch.object(wallet_topups.stars_checkout,'create_invoice_link') as provider:
            with self.assertRaises(HTTPException) as error:wallet_topups.create('dsn',{'id':uuid4()},SimpleNamespace(currency='XTR',amount=10))
            self.assertEqual(error.exception.detail,'wallet_stars_topup_retired');db.assert_not_called();provider.assert_not_called()
    def test_discounts_do_not_change_stars_and_telegram_ignores_automatic_discounts(self):
        conn=MagicMock();offer=dict(id=uuid4(),code=None,percent=20,total_limit=None,per_user_limit=1,name='Discount')
        conn.execute.return_value.fetchall.return_value=[offer];conn.execute.return_value.fetchone.return_value={'total':0,'own':0}
        tariff=dict(id=uuid4(),price_kopecks=30000,stars_price_xtr=151,duration_seconds=3600)
        user={'id':uuid4()};q=growth.quote(conn,user,tariff)
        self.assertEqual((q['price_kopecks'],q['stars_price_xtr']),(24000,151))
        q=growth.quote(conn,{**user,'login_surface':'telegram'},tariff)
        self.assertEqual((q['price_kopecks'],q['stars_price_xtr'],q['offer_id']),(30000,151,None))
        with self.assertRaises(HTTPException) as error:growth.quote(conn,{**user,'login_surface':'telegram'},tariff,'GIFT')
        self.assertEqual(error.exception.detail,'growth_rub_only')

@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class RubleReferral(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    def test_stars_completion_never_awards_coupon_or_bonus(self):
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            for mode in ('coupon','bonus'):
                inviter,friend,ch,t,c=[uuid4() for _ in range(5)]
                for uid in (inviter,friend):conn.execute('INSERT INTO users(id,telegram_user_id) VALUES(%s,%s)',(uid,int(uid.hex[:12],16)))
                conn.execute('INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,reward_mode) VALUES(%s,%s,10,30,%s)',(friend,inviter,mode))
                conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Channel',%s)",(ch,friend,'https://t.me/test_'+ch.hex))
                conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,currency) VALUES(%s,'Test',3600,10000,'RUB')",(t,))
                conn.execute("INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot) VALUES(%s,%s,%s,%s,'Test',3600,10000,'RUB')",(c,friend,ch,t))
                conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,provider_payment_id) VALUES(%s,'telegram_stars','telegram_bot',100,'XTR','succeeded',%s)",(c,str(uuid4())))
                conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='verified',placement_status='completed' WHERE id=%s",(c,))
                self.assertFalse(conn.execute('SELECT 1 FROM growth_offers WHERE reward_for_user_id=%s',(friend,)).fetchone())
                self.assertFalse(conn.execute('SELECT 1 FROM growth_wallet_rewards WHERE referred_user_id=%s',(friend,)).fetchone())
