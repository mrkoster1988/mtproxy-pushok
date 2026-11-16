"""Покупка, неизменность условий и квоты лицензии на настоящем PostgreSQL."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import licenses
from bot.main import process_stars_update

main=fixture.main

class LicenseUnitTests(unittest.TestCase):
    def test_strict_plan_and_canonical_payload(self):
        uid=uuid4()
        self.assertEqual(licenses.parse_order('license:'+str(uid)),uid)
        for payload in ('wallet:'+str(uid),'license:'+uid.hex,None,'license:bad'):
            self.assertIsNone(licenses.parse_order(payload))
        for changes in ({'days':0},{'price_xtr':-1},{'max_channels':-1},{'max_channels':True},{'active':'yes'},{'name':' '}):
            with self.assertRaises(ValueError):licenses.Plan(**{**dict(name='Тариф',days=30,price_xtr=10),**changes})
        self.assertIsNone(licenses.Plan(name='Тариф',days=30,price_xtr=10).max_channels)

    def test_bot_dispatches_license_events_and_reports_correct_purchase(self):
        calls=[];sent=[];payload='license:'+str(uuid4())
        def api(route,data):
            calls.append((route,data));return {'ok':True,'status':'recorded' if route.endswith('refund') else 'applied'}
        def send(*args,**kwargs):sent.append(args)
        details={'invoice_payload':payload,'currency':'XTR','total_amount':10,'telegram_payment_charge_id':'license-charge'}
        process_stars_update({'pre_checkout_query':{'id':'query','from':{'id':42},**details}},'test',internal_api=api,send=send)
        for kind in ('successful_payment','refunded_payment'):
            process_stars_update({'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},kind:details}},'test',internal_api=api,send=send)
        self.assertEqual([c[0] for c in calls],['license/precheckout','license/settle','license/refund'])
        self.assertIn('Оплата лицензии подтверждена',sent[1][2]['text'])
        self.assertNotIn('размещения',sent[1][2]['text'])

@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class LicensePostgresTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.admin,self.owner,self.other,self.tariff,self.channel=[uuid4() for _ in range(5)]
        self.tg=int(self.owner.hex[:12],16)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE license_settings SET enabled=false')
            conn.execute('UPDATE license_plans SET active=false')
            for i,(uid,role) in enumerate(((self.admin,'admin'),(self.owner,'advertiser'),(self.other,'advertiser'))):
                conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',(uid,self.tg+i,role))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'Размещение',3600,100)",(self.tariff,))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал',%s)",(self.channel,self.owner,'https://t.me/license_'+self.channel.hex))
        self.actor={'id':self.admin,'role':'admin'}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)
        for p in (patch.dict(os.environ,{'BOT_INTERNAL_SECRET':'a'*64,'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0'}),
                  patch.object(licenses,'stars_invoices_enabled',return_value=True),
                  patch.object(licenses.stars_checkout,'credentials',return_value=('123456:synthetic_only_token',123456)),
                  patch.object(licenses.stars_checkout,'create_invoice_link',return_value='https://t.me/$license_test')):
            p.start();self.addCleanup(p.stop)
        self.addCleanup(self.disable)
        self.headers={'X-Pushok-Bot-Secret':'a'*64}
        self.plan_body=dict(name='Кабинет',days=30,price_xtr=100,max_channels=2,max_open_campaigns=1,max_campaigns_per_period=2,active=True)

    def disable(self):
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE license_settings SET enabled=false')

    def as_admin(self):self.actor={'id':self.admin,'role':'admin'}
    def as_owner(self):self.actor={'id':self.owner,'role':'advertiser'}
    def plan(self,**changes):
        self.as_admin();self.plan_body.update(changes)
        p=self.request('POST','/admin/licenses/plans',201,json=self.plan_body)
        self.request('PUT','/admin/licenses/settings',json={'enabled':True});self.as_owner();return p
    def checkout(self,p):return self.request('POST','/licenses/checkout',json={'plan_id':p['id'],'revision':p['revision']})
    def payment(self,o,**changes):
        return {**dict(telegram_user_id=self.tg+1,invoice_payload='license:'+o['order_id'],currency='XTR',total_amount=100),**changes}
    def precheck(self,o,expected=True,**changes):
        data=self.payment(o,query_id='q-'+o['order_id'],**changes)
        self.assertEqual(self.request('POST','/internal/bot/license/precheckout',headers=self.headers,json=data)['ok'],expected)
    def settle(self,o,charge=None,**changes):
        data=self.payment(o,telegram_payment_charge_id=charge or 'charge-'+o['order_id'],**changes)
        return self.request('POST','/internal/bot/license/settle',headers=self.headers,json=data)
    def buy(self,**plan_changes):
        p=self.plan(**plan_changes);o=self.checkout(p);self.precheck(o,total_amount=p['price_xtr']);self.assertEqual(self.settle(o,total_amount=p['price_xtr'])['status'],'applied');return p,o
    def grants(self):return self.request('GET','/licenses')['grants']
    def channel_new(self,expected=201):
        return self.request('POST','/channels',expected,json={'title':'Ещё канал','telegram_url':'https://t.me/lic_'+uuid4().hex})
    def campaign(self,expected=201):
        return self.request('POST','/campaigns',expected,json={'channel_id':str(self.channel),'tariff_id':str(self.tariff)})

    def test_off_by_default_and_admin_only_configuration(self):
        self.as_owner();self.assertFalse(self.request('GET','/licenses')['enabled']);self.channel_new()
        self.request('GET','/admin/licenses',403)
        self.request('PUT','/admin/licenses/settings',403,json={'enabled':True})
        self.as_admin();self.request('PUT','/admin/licenses/settings',409,json={'enabled':True})
        p=self.plan()
        self.assertEqual(self.channel_new(409)['detail'],'license_required')
        self.assertEqual(self.campaign(409)['detail'],'license_required')
        self.as_admin();self.channel_new()
        self.request('PUT','/admin/licenses/plans/'+p['id'],409,json={**self.plan_body,'active':False})

    def test_payment_snapshot_renewal_and_duplicate_charge(self):
        p=self.plan();o=self.checkout(p)
        self.assertEqual(self.checkout(p)['order_id'],o['order_id'])
        self.assertEqual(self.grants(),[])
        self.as_admin();changed=self.request('PUT','/admin/licenses/plans/'+p['id'],json={**self.plan_body,'price_xtr':200,'max_channels':9})
        self.as_owner();self.precheck(o,total_amount=200,expected=False);self.precheck(o)
        self.assertEqual(self.settle(o)['status'],'applied');self.assertEqual(self.settle(o)['status'],'already_processed')
        g=self.grants();self.assertEqual(len(g),1);self.assertEqual(g[0]['terms']['price_xtr'],100);self.assertEqual(g[0]['terms']['max_channels'],2)
        newer=self.checkout(changed);self.precheck(newer,total_amount=200);self.settle(newer,total_amount=200)
        g=self.grants();self.assertEqual(len(g),2);self.assertEqual(g[0]['ends_at'],g[1]['starts_at']);self.assertEqual(g[1]['terms']['max_channels'],9)
        self.settle(newer,total_amount=200);self.assertEqual(len(self.grants()),2)

    def test_invalid_receipts_foreign_invoice_and_secret(self):
        p=self.plan();o=self.checkout(p)
        data=self.payment(o,query_id='unauthorized')
        self.request('POST','/internal/bot/license/precheckout',403,json=data)
        self.precheck(o,telegram_user_id=self.tg+2,expected=False)
        self.assertEqual(self.settle(o,charge='before-check-'+o['order_id'])['status'],'requires_review');self.assertEqual(self.grants(),[])
        self.actor={'id':self.other,'role':'advertiser'}
        self.request('POST','/licenses/orders/'+o['order_id']+'/resume',404)
        self.request('POST','/licenses/orders/'+o['order_id']+'/close',404)
        self.as_owner();self.precheck(o);self.request('POST','/licenses/orders/'+o['order_id']+'/close',409)
        self.assertEqual(self.settle(o,total_amount=999,charge='wrong-'+o['order_id'])['status'],'requires_review');self.assertEqual(self.grants(),[])
        self.assertEqual(self.settle(o)['status'],'applied')
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM license_grants WHERE user_id=%s',(self.owner,)).fetchone()[0],1)

    def test_quota_archive_restore_period_and_expiry(self):
        self.buy()
        extra=self.channel_new();self.assertEqual(self.channel_new(409)['detail'],'license_channels_limit')
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE sponsor_channels SET archived_at=now() WHERE id=%s',(extra['id'],))
        self.channel_new()
        with self.assertRaisesRegex(psycopg.Error,'license_channels_limit'):
            with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE sponsor_channels SET archived_at=NULL WHERE id=%s',(extra['id'],))
        first=self.campaign();self.assertEqual(self.campaign(409)['detail'],'license_open_campaigns_limit')
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE campaigns SET placement_status='completed',archived_at=now() WHERE id=%s",(first['id'],))
        second=self.campaign()
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE campaigns SET placement_status='cancelled',archived_at=now() WHERE id=%s",(second['id'],))
        self.assertEqual(self.campaign(409)['detail'],'license_period_limit')
        self.assertEqual(self.request('GET','/licenses')['usage']['campaigns_per_period'],2)
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE license_grants SET starts_at=now()-interval '31 days',ends_at=now()-interval '1 day' WHERE user_id=%s",(self.owner,))
        self.assertEqual(self.campaign(409)['detail'],'license_required')
        # Истечение лицензии не блокирует историю и переходы уже созданных кампаний.
        self.request('GET','/campaigns')
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET moderator_comment='После истечения' WHERE id=%s",(first['id'],))
            self.assertEqual(conn.execute('SELECT placement_status FROM campaigns WHERE id=%s',(first['id'],)).fetchone()[0],'completed')

    def test_concurrent_limit_and_zero_unlimited_semantics(self):
        self.buy(max_channels=2,max_open_campaigns=0,max_campaigns_per_period=None)
        def add(_):
            try:
                with psycopg.connect(self.dsn) as conn:conn.execute("INSERT INTO sponsor_channels(owner_user_id,title,telegram_url) VALUES(%s,'Параллельно',%s)",(self.owner,'https://t.me/concurrent_'+uuid4().hex))
                return 'created'
            except psycopg.Error as exc:return exc.diag.message_primary
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(add,range(4)))
        self.assertEqual(results.count('created'),1);self.assertEqual(results.count('license_channels_limit'),3)
        self.assertEqual(self.campaign(409)['detail'],'license_open_campaigns_limit')

    def test_free_plan_once_and_test_environment_does_not_activate(self):
        p=self.plan(price_xtr=0,max_channels=None,max_open_campaigns=None,max_campaigns_per_period=None)
        with patch.object(licenses.stars_checkout,'credentials',side_effect=AssertionError('free needs no credentials')):
            self.assertEqual(self.checkout(p)['status'],'paid')
        self.assertEqual(len(self.grants()),1);self.channel_new();self.channel_new()
        self.request('POST','/licenses/checkout',409,json={'plan_id':p['id'],'revision':p['revision']})
        self.actor={'id':self.other,'role':'advertiser'}
        with patch.dict(os.environ,{'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):
            self.assertTrue(self.checkout(p)['test_mode']);self.assertEqual(self.grants(),[])
        # Тестовая бесплатная покупка не расходует реальный пробный период.
        self.checkout(p);self.assertEqual(len(self.grants()),1)

    def test_paid_test_mode_never_grants_production_license(self):
        with patch.dict(os.environ,{'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):
            self.headers['X-Pushok-Telegram-Environment']='test'
            self.buy();self.assertEqual(self.grants(),[])

    def test_refund_after_payment_and_before_receipt(self):
        p,o=self.buy()
        refund=dict(telegram_user_id=self.tg+1,currency='XTR',total_amount=100,telegram_payment_charge_id='charge-'+o['order_id'])
        self.request('POST','/internal/bot/license/refund',headers=self.headers,json=refund)
        self.request('POST','/internal/bot/license/refund',headers=self.headers,json=refund)
        self.assertEqual(self.grants(),[]);self.assertEqual(self.settle(o)['status'],'already_processed');self.assertEqual(self.grants(),[])
        next_order=self.checkout(p);self.precheck(next_order)
        refund['telegram_payment_charge_id']='charge-'+next_order['order_id']
        self.request('POST','/internal/bot/license/refund',headers=self.headers,json=refund)
        self.settle(next_order);self.assertEqual(self.grants(),[])
        self.assertEqual([o['status'] for o in self.request('GET','/licenses')['orders']],['refunded','refunded'])

    def test_quote_expiry_cancel_and_old_quote_resume_after_edit(self):
        p=self.plan();o=self.checkout(p)
        self.as_admin();self.request('PUT','/admin/licenses/plans/'+p['id'],json={**self.plan_body,'price_xtr':200});self.as_owner()
        self.assertEqual(self.request('POST','/licenses/orders/'+o['order_id']+'/resume')['invoice_url'],'https://t.me/$license_test')
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE license_orders SET expires_at=now()-interval '1 second' WHERE id=%s",(o['order_id'],))
        self.precheck(o,expected=False)
        self.request('POST','/licenses/orders/'+o['order_id']+'/resume',409)
        self.request('POST','/licenses/orders/'+o['order_id']+'/close')
        self.request('POST','/licenses/checkout',409,json={'plan_id':p['id'],'revision':p['revision']})
