"""Реальные транзакции скидок, подарков и реферальных наград в изолированной БД."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException
import test_postgres as fixture
main = fixture.main
from app.growth import discounted, OfferInput
from app.order_submission import create_order
from app.bot_stars import prepare_for_user, StarsPrepareInput


class GrowthPrices(unittest.TestCase):
    def test_integer_prices_no_currency_conversion(self):
        self.assertEqual(discounted(30000,20,rub=True),24000)
        self.assertEqual(discounted(151,20),121)
        self.assertEqual(discounted(101,99,rub=True),100)
        self.assertEqual(discounted(30000,100,rub=True),0)
        self.assertIsNone(discounted(None,20))
        self.assertEqual(discounted(0,10,rub=True),0)

    def test_offer_validation(self):
        with self.assertRaises(ValueError):
            OfferInput(kind='gift',name='Подарок',percent=100)
        with self.assertRaises(ValueError):
            OfferInput(kind='promo',name='Промо',percent=10,code='bad code')
        with self.assertRaises(ValueError):
            OfferInput(kind='discount',name=' ',percent=10)
        with self.assertRaises(ValueError):
            OfferInput(kind='discount',name='Test',percent=10,ends_at=datetime.now())

    def test_bot_referral_private_identity(self):
        from bot.main import response_for
        from unittest.mock import Mock
        handler=Mock()
        update={'message':{'text':'/start ref_ABCDEF','from':{'id':123},'chat':{'id':123,'type':'private'}}}
        result=response_for(update,referral_handler=handler)
        handler.assert_called_once_with(123,'ABCDEF')
        self.assertIn('Приглашение сохранено',result[0]['text'])
        handler.reset_mock();update['message']['text']='/start r_ABCDEFGH'
        result=response_for(update,referral_handler=handler)
        handler.assert_called_once_with(123,'ABCDEFGH')
        self.assertIn('Приглашение сохранено',result[0]['text'])
        update['message']['chat']['type']='group'
        handler.reset_mock();response_for(update,referral_handler=handler);handler.assert_not_called()


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class GrowthIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.admin,self.owner,self.friend,self.tariff=[uuid4() for _ in range(4)]
        self.telegram=int(uuid4().hex[:12],16)
        with psycopg.connect(self.dsn) as conn:
            for uid,tg,role in [(self.admin,self.telegram+1,'admin'),(self.owner,self.telegram,'advertiser'),(self.friend,self.telegram+2,'advertiser')]:
                conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',(uid,tg,role))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr,currency) VALUES(%s,'Тестовый',86400,30000,151,'RUB')",(self.tariff,))
            conn.execute('UPDATE growth_settings SET referrals_enabled=false,reward_percent=10,reward_days=90')
        self.actor={'id':self.admin,'role':'admin'}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)

    def test_short_link_is_stable_and_legacy_invitation_still_works(self):
        self.request('PUT','/admin/growth/settings',json={'referrals_enabled':True,'reward_percent':10,'reward_days':30})
        self.actor={'id':self.owner,'role':'advertiser'}
        mine=self.request('GET','/growth/me')
        self.assertRegex(mine['code'],r'^[A-Z2-9]{8}$')
        self.assertTrue(mine['link'].endswith('?start=r_'+mine['code']))
        self.assertEqual(self.request('GET','/growth/me')['link'],mine['link'])
        with psycopg.connect(self.dsn) as conn:
            legacy=conn.execute('SELECT code FROM growth_profiles WHERE user_id=%s',(self.owner,)).fetchone()[0]
        self.assertEqual(len(legacy),32)
        self.request('POST','/growth/referral',409,json={'code':mine['code']})
        self.actor={'id':self.friend,'role':'advertiser'}
        self.request('POST','/growth/referral',json={'code':mine['code'].lower()})
        self.request('POST','/growth/referral',json={'code':legacy})
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT inviter_user_id FROM growth_referrals WHERE referred_user_id=%s',(self.friend,)).fetchone()[0],self.owner)

    def test_short_alias_collision_and_concurrent_creation(self):
        from psycopg.rows import dict_row
        from app.growth import short_referral_code
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute('INSERT INTO growth_profiles(user_id) VALUES(%s)',(self.owner,))
            conn.execute("INSERT INTO growth_profiles(user_id,code) VALUES(%s,'JKLMNPQR')",(self.friend,))
            conn.execute("INSERT INTO growth_referral_links(user_id,code) VALUES(%s,'ABCDEFGH')",(self.friend,))
            with patch('app.growth.secrets.choice',side_effect=list('ABCDEFGHJKLMNPQRSTUVWXY2')):
                self.assertEqual(short_referral_code(conn,self.owner),'STUVWXY2')
            conn.execute('INSERT INTO growth_profiles(user_id) VALUES(%s)',(self.admin,))
        def create():
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                return short_referral_code(conn,self.admin)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:create(),range(4)))
        self.assertEqual(len(set(results)),1)

    def offer(self,kind='promo',**kw):
        body={'kind':kind,'name':'Тестовый бонус','percent':20,'tariff_id':str(self.tariff),'code':'P'+uuid4().hex.upper(),'total_limit':1}
        if kind=='discount':body['code']=None
        if kind=='gift':body.update(percent=100,code=None)
        body.update(kw)
        return self.request('POST','/admin/growth/offers',201,json=body)

    def quote(self,code=None,expected=200):
        return self.request('POST','/growth/quote',expected,json={'tariff_id':str(self.tariff),'code':code})

    def payload(self,q,code=None):
        return {'request_id':str(uuid4()),'channel':{'title':'Тестовый канал','telegram_url':'https://t.me/test_'+uuid4().hex[:12]},'tariff_id':str(self.tariff),'expected_duration_seconds':86400,'expected_price_kopecks':q['price_kopecks'],'quote_revision':q['revision'],'promo_code':code}

    def test_shared_offer_authorization_expiry_and_no_redemption_on_open(self):
        offer=self.offer()
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'telegram'}
        path='/growth/shared/'+offer['id']
        shared=self.request('GET',path)
        self.assertEqual(shared['code'],offer['code'])
        self.assertEqual(shared['quote']['stars_price_xtr'],121)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE offer_id=%s',(offer['id'],)).fetchone()[0],0)
            conn.execute('UPDATE growth_offers SET recipient_user_id=%s WHERE id=%s',(self.owner,offer['id']))
        self.actor={'id':self.friend,'role':'advertiser'}
        self.request('GET',path,409)
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'telegram'}
        shared=self.request('GET',path)
        order=self.request('POST','/orders',201,json=self.payload(shared['quote'],shared['code']))
        self.assertEqual(order['stars_price_snapshot'],121)
        self.request('GET',path,409)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE growth_offers SET active=false WHERE id=%s',(offer['id'],))
        self.request('GET',path,409)

    def test_admin_discount_replaces_shared_promo_without_stacking(self):
        offer=self.offer();self.actor={'id':self.owner,'role':'advertiser','login_surface':'telegram'}
        campaign=self.request('POST','/orders',201,json=self.payload(self.quote(offer['code']),offer['code']))
        self.actor={'id':self.admin,'role':'admin'}
        path='/admin/campaigns/'+campaign['id']+'/pricing'
        before=self.request('GET',path)
        self.assertEqual(before['current']['stars_price_xtr'],121)
        self.assertEqual(before['current']['base_stars_price_xtr'],151)
        after=self.request('PATCH',path,json=dict(revision=before['revision'],discount_percent=10,reason='Согласованная скидка'))
        self.assertEqual(after['current']['stars_price_xtr'],136)

    def test_shared_automatic_offer_selects_eligible_tariff(self):
        offer=self.offer('discount',percent=30,tariff_id=str(self.tariff))
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'telegram'}
        result=self.request('GET','/growth/shared/'+offer['id'])
        self.assertIsNone(result['code'])
        self.assertEqual(result['tariff_id'],str(self.tariff))
        self.assertEqual(result['quote']['stars_price_xtr'],106)

    def test_price_snapshot_retry_and_stars_invoice(self):
        offer=self.offer();self.actor={'id':self.owner,'role':'advertiser'}
        q=self.quote(offer['code']);self.assertEqual(q['price_kopecks'],24000);self.assertEqual(q['stars_price_xtr'],121)
        body=self.payload(q,offer['code']);c=self.request('POST','/orders',201,json=body)
        retry=self.request('POST','/orders',201,json=body);self.assertEqual(c['id'],retry['id'])
        self.quote(offer['code'],409)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE campaign_id=%s',(c['id'],)).fetchone()[0],1)
            conn.execute('UPDATE tariffs SET price_kopecks=90000,stars_price_xtr=900 WHERE id=%s',(self.tariff,))
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(c['id'],))
        with patch('app.bot_stars.stars_invoices_enabled',return_value=True):
            prepared=prepare_for_user(self.dsn,StarsPrepareInput(campaign_id=c['id'],telegram_user_id=self.telegram))
        self.assertEqual(prepared.stars_amount,121)
        self.assertEqual(self.request('GET','/campaigns')[0]['price_kopecks_snapshot'],24000)

    def test_gift_zero_cost_moderated_once_concurrent(self):
        gift=self.offer('gift');self.actor={'id':self.owner,'role':'advertiser'}
        q=self.quote(gift['code']);self.assertEqual(q['price_kopecks'],0)
        bodies=[main.OrderSubmission(**self.payload(q,gift['code'])) for _ in range(2)]
        def submit(body):
            try:return create_order(self.dsn,body,self.actor)
            except HTTPException as e:return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,bodies))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertIn(409,results)
        c=next(r for r in results if isinstance(r,dict));self.assertEqual(c['moderation_status'],'pending')
        self.actor={'id':self.admin,'role':'admin'}
        approved=self.request('POST','/operator/campaigns/'+str(c['id'])+'/moderation',json={'decision':'approved'})
        self.assertEqual(approved['payment_status'],'verified')
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM payment_attempts WHERE campaign_id=%s',(c['id'],)).fetchone()[0],0)
        self.actor={'id':self.friend,'role':'advertiser'};self.quote(gift['code'],409)
        self.request('POST','/admin/growth/offers',403,json={'kind':'discount','name':'No','percent':100})

    def test_discounted_date_edit_preserves_price(self):
        promo=self.offer();self.actor={'id':self.owner,'role':'advertiser'}
        c=self.request('POST','/orders',201,json=self.payload(self.quote(promo['code']),promo['code']))
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET moderation_status='changes_requested' WHERE id=%s",(c['id'],))
            conn.execute('UPDATE tariffs SET price_kopecks=90000 WHERE id=%s',(self.tariff,))
        self.request('PATCH','/campaigns/'+c['id'],json={'tariff_id':str(self.tariff),'requested_start_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()})
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT price_kopecks_snapshot,stars_price_snapshot FROM campaigns WHERE id=%s',(c['id'],)).fetchone(),(24000,121))
        self.request('PATCH','/campaigns/'+c['id'],409,json={'tariff_id':str(uuid4())})

    def test_best_discount_no_stack_and_changed_quote(self):
        auto=self.offer('discount',percent=30,min_duration_seconds=86400,total_limit=None)
        promo=self.offer(percent=10);self.actor={'id':self.owner,'role':'advertiser'}
        q=self.quote(promo['code']);self.assertEqual(q['offer_id'],auto['id']);self.assertEqual(q['price_kopecks'],21000)
        body=self.payload(q,promo['code'])
        self.actor={'id':self.admin,'role':'admin'}
        self.request('PATCH','/admin/growth/offers/'+auto['id'],json={'active':False})
        self.actor={'id':self.owner,'role':'advertiser'}
        self.request('POST','/orders',409,json=body)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE user_id=%s',(self.owner,)).fetchone()[0],0)
        self.assertEqual(self.quote(promo['code'])['price_kopecks'],27000)
        self.actor={'id':self.admin,'role':'admin'}
        future=self.offer(starts_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        self.actor={'id':self.owner,'role':'advertiser'};self.quote(future['code'],409)

    def test_referral_reward_once_and_refund_revocation(self):
        self.request('PUT','/admin/growth/settings',json={'referrals_enabled':True,'reward_percent':25,'reward_days':30})
        self.actor={'id':self.owner,'role':'advertiser'};mine=self.request('GET','/growth/me')
        self.request('POST','/growth/referral',409,json={'code':mine['code']})
        self.actor={'id':self.friend,'role':'advertiser'}
        self.request('POST','/growth/referral',json={'code':mine['code']})
        self.request('POST','/growth/referral',json={'code':mine['code']})
        friend=self.request('GET','/growth/me')
        self.actor={'id':self.owner,'role':'advertiser'}
        self.request('POST','/growth/referral',409,json={'code':friend['code']})
        self.actor={'id':self.friend,'role':'advertiser'}
        c=self.request('POST','/orders',201,json=self.payload(self.quote()))
        with psycopg.connect(self.dsn) as conn:
            # Настоящее подтверждение провайдера моделируется записью успешного счёта.
            conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,provider_payment_id,sbp_test_mode,settled_at) VALUES(%s,'sbp','external_web',30000,'RUB','succeeded',%s,false,now())",(c['id'],uuid4().hex))
            conn.execute("UPDATE campaigns SET moderation_status='approved',payment_status='verified',placement_status='completed' WHERE id=%s",(c['id'],))
            conn.execute("UPDATE campaigns SET updated_at=now() WHERE id=%s",(c['id'],))
        self.actor={'id':self.owner,'role':'advertiser'};mine=self.request('GET','/growth/me')
        self.assertEqual(len(mine['coupons']),1);self.assertEqual(mine['coupons'][0]['percent'],25)
        code=mine['coupons'][0]['code'];self.assertEqual(self.quote(code)['price_kopecks'],22500)
        self.actor={'id':self.friend,'role':'advertiser'};self.quote(code,409)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET payment_status='refunded' WHERE id=%s",(c['id'],))
        self.actor={'id':self.owner,'role':'advertiser'};self.quote(code,409)
        self.assertFalse(self.request('GET','/growth/me')['coupons'][0]['active'])

    def test_free_tariff_does_not_consume_coupon(self):
        promo=self.offer();self.actor={'id':self.owner,'role':'advertiser'}
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE tariffs SET price_kopecks=0,stars_price_xtr=NULL WHERE id=%s',(self.tariff,))
        self.quote(promo['code'],409)
        q=self.quote();self.assertIsNone(q['offer_id']);self.assertEqual(q['price_kopecks'],0)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE offer_id=%s',(promo['id'],)).fetchone()[0],0)

    def test_bot_invite_requires_secret_and_no_reward_for_free(self):
        self.request('PUT','/admin/growth/settings',json={'referrals_enabled':True,'reward_percent':10,'reward_days':30})
        self.actor={'id':self.owner,'role':'advertiser'};code=self.request('GET','/growth/me')['code']
        with patch.dict(os.environ,{'BOT_INTERNAL_SECRET':'c'*64}):
            body={'telegram_user_id':self.telegram+3,'code':code}
            self.request('POST','/internal/bot/referral',401,json=body)
            self.request('POST','/internal/bot/referral',json=body,headers={'X-Pushok-Bot-Secret':'c'*64})
        with psycopg.connect(self.dsn) as conn:
            referred=conn.execute('SELECT id FROM users WHERE telegram_user_id=%s',(self.telegram+3,)).fetchone()[0]
        self.actor={'id':self.admin,'role':'admin'};gift=self.offer('gift')
        self.actor={'id':referred,'role':'advertiser'}
        c=self.request('POST','/orders',201,json=self.payload(self.quote(gift['code']),gift['code']))
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET moderation_status='approved',placement_status='completed' WHERE id=%s",(c['id'],))
        self.actor={'id':self.owner,'role':'advertiser'}
        self.assertEqual(self.request('GET','/growth/me')['coupons'],[])
