"""Лицензия владельца установки: подписи, Stars и глобальные ограничения."""
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import application_licenses as licensing,license_crypto as crypto
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

main=fixture.main

class LicenseCryptoTests(unittest.TestCase):
    def test_signature_binding_tampering_and_clock(self):
        key=Ed25519PrivateKey.generate();iid,pub=crypto.identity(key)
        payload=dict(installation_id=iid,public_key=pub,issued_at=int(time.time()),nonce=uuid4().hex,operation='status')
        signed=crypto.sign(key,payload)
        self.assertEqual(crypto.check_request(signed),payload)
        with self.assertRaises(ValueError):crypto.verify(crypto.identity(Ed25519PrivateKey.generate())[1],signed)
        signed['payload']['installation_id']='0'*64
        with self.assertRaises(ValueError):crypto.check_request(signed)
        with self.assertRaises(ValueError):crypto.check_request(crypto.sign(key,{**payload,'issued_at':0}))

    def test_bot_routes_license_payments_and_refunds(self):
        from bot.main import process_stars_update
        sent=[];routes=[]
        def api(route,payload):
            routes.append(route)
            return {'ok':True,'status':'recorded' if route.endswith('/refund') else 'applied','test_mode':False}
        def send(*args,**kw):sent.append(args)
        data=dict(invoice_payload='license:'+str(uuid4()),currency='XTR',total_amount=100,telegram_payment_charge_id='license-fixture')
        self.assertTrue(process_stars_update({'pre_checkout_query':{'id':'q','from':{'id':42},**data}},'fixture',internal_api=api,send=send))
        self.assertTrue(process_stars_update({'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'successful_payment':data}},'fixture',internal_api=api,send=send))
        self.assertIn('Лицензия приложения',sent[-1][2]['text'])
        self.assertTrue(process_stars_update({'message':{'chat':{'id':42,'type':'private'},'refunded_payment':data}},'fixture',internal_api=api,send=send))
        self.assertEqual(routes,['license/precheckout','license/settle','license/refund'])

    def test_plan_rejects_invalid_limits(self):
        for extra in ({'price_xtr':0},{'max_admins':0},{'days':False},{'max_nodes':-1},{'name':' '}):
            with self.assertRaises(ValueError):licensing.Plan(**{**dict(name='Установка',price_xtr=100,days=30),**extra})
        self.assertIsNone(licensing.Plan(name='Установка',price_xtr=100,days=30).max_nodes)

@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class ApplicationLicenses(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.admin,self.user,self.tariff,self.channel=[uuid4() for _ in range(4)];self.tg=int(self.admin.hex[:12],16)
        self.install_key=Ed25519PrivateKey.generate();self.iid,self.pub=crypto.identity(self.install_key)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE app_license_state SET required=false,envelope=NULL')
            conn.execute('UPDATE app_license_settings SET enabled=false');conn.execute('UPDATE app_license_plans SET active=false')
            for i,(uid,role) in enumerate(((self.admin,'admin'),(self.user,'advertiser'))):conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',(uid,self.tg+i,role))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'Реклама',3600,100)",(self.tariff,))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал',%s)",(self.channel,self.user,'https://t.me/app_license_'+self.channel.hex))
        self.actor={'id':self.admin,'role':'admin'};main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear);self.addCleanup(self.disable)
        for p in (patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'seller','BOT_INTERNAL_SECRET':'a'*64,'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0'}),patch.object(licensing,'stars_invoices_enabled',return_value=True),patch.object(licensing.stars_checkout,'credentials',return_value=('123456:synthetic_only_token',123456)),patch.object(licensing.stars_checkout,'create_invoice_link',return_value='https://t.me/$app_license_test')):
            p.start();self.addCleanup(p.stop)
        self.headers={'X-Pushok-Bot-Secret':'a'*64};self.plan_body=dict(name='Приложение',days=30,price_xtr=100,max_nodes=2,max_admins=None,max_campaigns=2,active=True)

    def disable(self):
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE app_license_state SET required=false')
    def envelope(self,op,**data):return crypto.sign(self.install_key,dict(installation_id=self.iid,public_key=self.pub,operation=op,nonce=uuid4().hex,issued_at=int(time.time()),**data))
    def store(self,op,expected=200,**data):
        envelope=self.envelope(op,**data);r=self.request('POST','/license-store/requests',expected,json=envelope)
        if expected!=200:return r
        payload=crypto.verify(licensing.public_key(),r);self.assertEqual(payload['nonce'],envelope['payload']['nonce']);return payload
    def plan(self,**changes):
        self.plan_body.update(changes);p=self.request('POST','/admin/license/plans',201,json=self.plan_body);self.request('PUT','/admin/license/settings',json={'enabled':True});return p
    def invoice(self,p):return self.store('checkout',plan_id=p['id'],revision=p['revision'],payer_id=self.tg)
    def paydata(self,o,**changes):return {**dict(invoice_payload='license:'+o['order_id'],telegram_user_id=self.tg,total_amount=100,currency='XTR'),**changes}
    def approve(self,o,expected=True,**changes):self.assertEqual(self.request('POST','/internal/bot/license/precheckout',headers=self.headers,json=self.paydata(o,query_id='q-'+o['order_id'],**changes))['ok'],expected)
    def settle(self,o,**changes):return self.request('POST','/internal/bot/license/settle',headers=self.headers,json=self.paydata(o,**{'telegram_payment_charge_id':'c-'+o['order_id'],**changes}))
    def purchase(self,**changes):
        p=self.plan(**changes);o=self.invoice(p);self.approve(o,total_amount=p['price_xtr']);self.assertEqual(self.settle(o,total_amount=p['price_xtr'])['status'],'applied');return p,o
    def install_cache(self):
        # Тот же путь проверки подписи, что и HTTP-клиент, с синтетическим транспортом.
        def transport(req,timeout):
            envelope=json.loads(req.data)
            with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'seller'}):response=self.request('POST','/license-store/requests',json=envelope)
            return BytesIO(json.dumps(response).encode())
        with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'client','PUSHOK_LICENSE_SERVER':'https://licenses.example.test','PUSHOK_LICENSE_PUBLIC_KEY':licensing.public_key()}),patch.object(crypto,'request',side_effect=self.envelope),patch.object(licensing,'build_opener') as opener:
            opener.return_value.open.side_effect=transport;licensing.sync(self.dsn)
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE app_license_state SET required=true,installation_id=%s',(self.iid,))

    def test_migration_is_repeatable_and_first_admin_can_buy(self):
        from pathlib import Path
        migration=Path(__file__).resolve().parents[1]/'database/045_application_licenses.sql'
        with psycopg.connect(self.dsn) as conn:
            conn.execute(migration.read_text());conn.execute(migration.read_text())
            conn.execute('UPDATE app_license_state SET required=true,envelope=NULL')
            # Существующий администратор продолжает входить даже без лицензии.
            conn.execute("UPDATE users SET role='admin' WHERE id=%s",(self.admin,))
        self.request('GET','/admin/license')

    def test_seller_configuration_and_client_cannot_edit_tariffs(self):
        self.request('PUT','/admin/license/settings',409,json={'enabled':True})
        p=self.plan();self.request('PUT','/admin/license/plans/'+p['id'],409,json={**self.plan_body,'active':False})
        self.actor={'id':self.user,'role':'advertiser'};self.request('GET','/admin/license',403);self.request('GET','/admin/license/status',403)
        self.request('POST','/admin/license/checkout',403,json={'plan_id':p['id'],'revision':p['revision']})
        self.actor={'id':self.admin,'role':'admin'}
        with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'client'}):self.request('POST','/admin/license/plans',404,json=self.plan_body)
        forged=self.envelope('status');forged['payload']['installation_id']='0'*64
        self.request('POST','/license-store/requests',403,json=forged)

    def test_fixed_quote_payment_renewal_and_duplicate(self):
        p=self.plan();o=self.invoice(p);self.assertEqual(self.invoice(p)['order_id'],o['order_id'])
        self.assertEqual(self.store('status')['grants'],[])
        updated=self.request('PUT','/admin/license/plans/'+p['id'],json={**self.plan_body,'price_xtr':200,'max_nodes':8})
        self.approve(o,expected=False,total_amount=200);self.approve(o)
        self.assertEqual(self.settle(o)['status'],'applied');self.assertEqual(self.settle(o)['status'],'already_processed')
        grants=self.store('status')['grants'];self.assertEqual(len(grants),1);self.assertEqual(grants[0]['terms']['max_nodes'],2)
        next_order=self.invoice(updated);self.approve(next_order,total_amount=200);self.settle(next_order,total_amount=200)
        grants=self.store('status')['grants'];self.assertEqual(len(grants),2);self.assertEqual(grants[0]['ends_at'],grants[1]['starts_at']);self.assertEqual(grants[1]['terms']['max_nodes'],8)

    def test_wrong_payer_receipt_no_precheckout_and_cross_installation(self):
        p=self.plan();o=self.invoice(p)
        self.request('POST','/internal/bot/license/precheckout',401,json=self.paydata(o,query_id='unauthorized'))
        self.approve(o,expected=False,telegram_user_id=self.tg+1)
        self.assertEqual(self.settle(o,telegram_payment_charge_id='bad-'+o['order_id'])['status'],'requires_review');self.assertEqual(self.store('status')['grants'],[])
        self.approve(o);self.assertEqual(self.settle(o)['status'],'applied')
        self.install_key=Ed25519PrivateKey.generate();self.iid,self.pub=crypto.identity(self.install_key)
        self.assertEqual(self.store('status')['grants'],[]);other=self.invoice(p);self.approve(other)
        self.assertEqual(self.settle(other,telegram_payment_charge_id='c-'+o['order_id'])['status'],'requires_review')
        self.assertEqual(self.store('status')['grants'],[])
        self.store('close',404,order_id=o['order_id'])

    def test_refund_ordering_and_test_mode(self):
        p,o=self.purchase()
        data=dict(telegram_user_id=self.tg,currency='XTR',total_amount=100,telegram_payment_charge_id='c-'+o['order_id'])
        self.request('POST','/internal/bot/license/refund',headers=self.headers,json=data);self.request('POST','/internal/bot/license/refund',headers=self.headers,json=data)
        self.assertEqual(self.store('status')['grants'],[]);self.settle(o);self.assertEqual(self.store('status')['grants'],[])
        other=self.invoice(p);self.approve(other);data['telegram_payment_charge_id']='c-'+other['order_id']
        self.request('POST','/internal/bot/license/refund',headers=self.headers,json=data);self.settle(other);self.assertEqual(self.store('status')['grants'],[])
        with patch.dict(os.environ,{'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):
            self.headers['X-Pushok-Telegram-Environment']='test';test=self.invoice(p);self.approve(test);self.assertTrue(self.settle(test)['test_mode']);self.assertEqual(self.store('status')['grants'],[])

    def test_expired_cancelled_and_closed_invoice(self):
        p=self.plan();o=self.invoice(p)
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE app_license_orders SET expires_at=now()-interval '1 second' WHERE id=%s",(o['order_id'],))
        self.approve(o,expected=False);self.store('close',order_id=o['order_id'])
        self.approve(o,expected=False);self.assertEqual(self.store('status')['orders'][0]['status'],'closed')

    def test_global_quota_concurrency_admins_and_expiry_preserve_campaign(self):
        with psycopg.connect(self.dsn) as conn:
            nodes=conn.execute('SELECT count(*) FROM proxy_nodes WHERE archived_at IS NULL').fetchone()[0]
            admins=conn.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0]
            campaigns=conn.execute("SELECT count(*) FROM campaigns WHERE archived_at IS NULL AND placement_status NOT IN ('completed','cancelled') AND moderation_status<>'rejected'").fetchone()[0]
        self.purchase(max_nodes=nodes+1,max_admins=admins,max_campaigns=campaigns+1);self.install_cache()
        def add(_):
            try:
                with psycopg.connect(self.dsn) as conn:conn.execute("INSERT INTO proxy_nodes(name,host,port) VALUES('Узел',%s,443)",(uuid4().hex+'.example.test',))
                return 'created'
            except psycopg.Error as exc:return exc.diag.message_primary
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(add,range(4)))
        self.assertEqual(results.count('created'),1);self.assertEqual(results.count('application_license_limit_max_nodes'),3)
        with self.assertRaisesRegex(psycopg.Error,'application_license_limit_max_admins'):
            with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE users SET role='admin' WHERE id=%s",(self.user,))
        self.actor={'id':self.user,'role':'advertiser'};body={'channel_id':str(self.channel),'tariff_id':str(self.tariff)}
        campaign=self.request('POST','/campaigns',201,json=body)
        self.assertEqual(self.request('POST','/campaigns',409,json=body)['detail'],'application_license_limit_max_campaigns')
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE app_license_state SET envelope=jsonb_set(envelope,'{payload,lease_until}','0')")
        self.assertEqual(self.request('POST','/campaigns',409,json=body)['detail'],'application_license_required')
        self.request('GET','/campaigns')
        with psycopg.connect(self.dsn) as conn:conn.execute("UPDATE campaigns SET placement_status='completed' WHERE id=%s",(campaign['id'],))

    def test_client_rejects_forged_response_and_keeps_last_verified_cache(self):
        self.purchase();self.install_cache()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:before=conn.execute('SELECT envelope FROM app_license_state').fetchone()['envelope']
        wrong=Ed25519PrivateKey.generate()
        def response(req,timeout):
            original=json.loads(req.data)['payload'];return BytesIO(json.dumps(crypto.sign(wrong,{**original,'grants':[],'lease_until':int(time.time())+1000})).encode())
        with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'client','PUSHOK_LICENSE_SERVER':'https://licenses.example.test','PUSHOK_LICENSE_PUBLIC_KEY':licensing.public_key()}),patch.object(crypto,'request',side_effect=self.envelope),patch.object(licensing,'build_opener') as opener:
            opener.return_value.open.side_effect=response
            with self.assertRaisesRegex(Exception,'license_server_unavailable'):licensing.sync(self.dsn)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:self.assertEqual(conn.execute('SELECT envelope FROM app_license_state').fetchone()['envelope'],before)
