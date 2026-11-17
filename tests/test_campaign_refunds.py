import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from uuid import uuid4, UUID
from unittest.mock import patch, Mock
from fastapi import HTTPException
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import refunds, wallet_topups as topups
from app.refunds import RefundInput


class Contracts(unittest.TestCase):
    def test_refund_validations(self):
        for n in (True,0,-1,1.2,100000001):
            with self.assertRaises(ValueError):RefundInput(request_id=uuid4(),amount=n,reason='Причина')
        with self.assertRaises(ValueError):RefundInput(request_id=uuid4(),reason='   ')
        for n in ('NaN','1.001','-1.00','1e2'):
            with self.assertRaises(Exception):refunds.cents(n)
        self.assertEqual(refunds.cents('12.50'),1250)



@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный PostgreSQL')
class FeaturesIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    setUp=wallet_fixture.WalletIntegration.setUp
    request=fixture.PostgresIntegration.request
    campaign=wallet_fixture.WalletIntegration.campaign
    credit=wallet_fixture.WalletIntegration.credit
    charge=wallet_fixture.WalletIntegration.charge
    balances=wallet_fixture.WalletIntegration.balances

    def admin_actor(self):self.actor={'id':self.admin,'role':'admin'}

    def paid(self,source='wallet'):
        cid=self.campaign()
        if source=='wallet':self.credit(amount=25000);self.credit('BONUS',5000);self.charge(cid)
        else:
            with topups.connect(self.dsn) as conn:
                if source!='manual':
                    conn.execute('''INSERT INTO payment_attempts(campaign_id,provider,payment_channel,currency,amount,status,provider_payment_id,payer_telegram_user_id,sbp_shop_id,sbp_test_mode)
                        VALUES(%s,%s,%s,%s,%s,'succeeded',%s,%s,'shop',true)''',
                        (cid,source,'telegram_bot' if source=='telegram_stars' else 'external_web','XTR' if source=='telegram_stars' else 'RUB',151 if source=='telegram_stars' else 30000,str(uuid4()),self.tg))
                conn.execute("UPDATE campaigns SET payment_status='verified' WHERE id=%s",(cid,))
        self.admin_actor();return cid

    def body(self,amount=None,**kw):return dict(request_id=str(uuid4()),amount=amount,reason='Запрос клиента',**kw)

    def test_wallet_partial_full_retry_and_old_route(self):
        cid=self.paid();path='/admin/campaigns/'+str(cid)+'/refunds';body=self.body(7000)
        r=self.request('POST',path,json=body);self.assertEqual(r['status'],'succeeded')
        self.assertEqual(self.balances()['BONUS'],5000);self.assertEqual(self.balances()['RUB'],2000)
        self.assertEqual(self.request('POST',path,json=body)['id'],r['id']);self.assertEqual(self.balances()['RUB'],2000)
        self.request('POST',path,409,json={**body,'amount':7001})
        self.request('POST',path,409,json=self.body(23001))
        self.request('POST','/admin/wallet/campaigns/'+str(cid)+'/refund',json={'reason':'Окончательный возврат'})
        self.assertEqual(self.balances()['RUB'],25000)
        self.assertEqual(self.request('GET',path)['remaining'],0)

    def test_concurrent_refunds_cannot_exceed_payment(self):
        cid=self.paid()
        def one():
            try:
                with topups.connect(self.dsn) as conn:return refunds.prepare(conn,cid,RefundInput(**self.body(20000)),self.admin)['id']
            except HTTPException as e:return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:one(),range(2)))
        self.assertEqual(results.count(409),1)
        rid=next(x for x in results if isinstance(x,UUID));refunds.process(self.dsn,rid)
        self.assertEqual(self.request('GET','/admin/campaigns/'+str(cid)+'/refunds')['remaining'],10000)

    def test_partial_refund_revokes_reward_once(self):
        with topups.connect(self.dsn) as conn:
            conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,reward_mode,reward_bonus_kopecks) VALUES(%s,%s,10,90,'bonus',1000)",(self.owner,self.other))
        cid=self.paid()
        with topups.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='completed' WHERE id=%s",(cid,))
            self.assertEqual(conn.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'",(self.other,)).fetchone()['balance'],1000)
        path='/admin/campaigns/'+str(cid)+'/refunds';body=self.body(100)
        self.request('POST',path,json=body);self.request('POST',path,json=body)
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'",(self.other,)).fetchone()['balance'],0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM wallet_entries WHERE user_id=%s AND kind='referral_reversal'",(self.other,)).fetchone()['n'],1)

    def test_roles_and_active_placement(self):
        cid=self.paid();path='/admin/campaigns/'+str(cid)+'/refunds';self.actor={'id':self.owner,'role':'advertiser'}
        self.request('GET',path,403);self.request('POST',path,403,json=self.body())
        self.admin_actor()
        with topups.connect(self.dsn) as conn:
            # Валидное scheduled-размещение с уже наступившим временем также блокирует возврат.
            node=uuid4()
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port,status) VALUES(%s,'Refund node','127.0.0.1',18443,'active')",(node,))
            conn.execute("UPDATE campaigns SET proxy_node_id=%s,placement_status='scheduled' ,scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '1 day' WHERE id=%s",(node,cid))
        self.request('POST',path,409,json=self.body())

    def test_manual_is_record_only_with_confirmation(self):
        cid=self.paid('manual');path='/admin/campaigns/'+str(cid)+'/refunds'
        self.request('POST',path,409,json=self.body(1000))
        r=self.request('POST',path,json=self.body(1000,manual_confirmed=True));self.assertEqual(r['status'],'succeeded')
        self.assertEqual(self.balances()['RUB'],0)

    def test_stars_only_full_and_unknown_result_retry(self):
        cid=self.paid('telegram_stars');path='/admin/campaigns/'+str(cid)+'/refunds'
        self.request('POST',path,409,json=self.body(1))
        body=self.body()
        with patch('app.refunds.stars_refund',side_effect=refunds.sbp.SBPProviderError('unavailable')):
            r=self.request('POST',path,json=body)
        self.assertEqual(r['status'],'review');self.assertEqual(self.request('GET',path)['refunded'],0)
        self.request('POST',path,409,json=self.body())
        with patch('app.refunds.stars_refund',return_value='succeeded') as provider:
            self.request('POST',path+'/'+r['id']+'/retry');self.request('POST',path+'/'+r['id']+'/retry')
            self.assertEqual(provider.call_count,1)
        self.assertEqual(self.request('GET',path)['refunded'],151)

    def test_sbp_partial_pending_get_identity_and_timeout(self):
        cid=self.paid('sbp');path='/admin/campaigns/'+str(cid)+'/refunds'
        with topups.connect(self.dsn) as conn:p=conn.execute('SELECT * FROM payment_attempts WHERE campaign_id=%s',(cid,)).fetchone()
        paid={'id':p['provider_payment_id'],'test':True,'status':'succeeded','paid':True,'payment_method':{'type':'sbp'},'metadata':{'campaign_id':str(cid)},'amount':{'value':'300.00','currency':'RUB'},'refunded_amount':{'value':'0.00','currency':'RUB'}}
        result={'id':'refund-one','payment_id':p['provider_payment_id'],'amount':{'value':'10.00','currency':'RUB'},'status':'pending'}
        self.request('POST',path,409,json=self.body(50));self.request('POST',path,409,json=self.body(29950))
        with patch('app.refunds.current',return_value={'shop':'shop','test_mode':True}),patch('app.refunds.sbp.get_sbp_payment',return_value=paid),patch('app.refunds.sbp._request',return_value=result) as transport:
            r=self.request('POST',path,json=self.body(1000));self.assertEqual(r['status'],'pending')
            key=transport.call_args.kwargs['idempotence_key'];self.assertEqual(key,r['id'])
            result['status']='succeeded'
            self.request('POST',path+'/'+r['id']+'/retry');self.assertEqual(transport.call_args.args[0],'GET')
        self.assertEqual(self.request('GET',path)['refunded'],1000)

    def test_sbp_mismatch_does_not_credit_and_expired_key_never_reposts(self):
        cid=self.paid('sbp');path='/admin/campaigns/'+str(cid)+'/refunds'
        with patch('app.refunds.sbp_refund',side_effect=HTTPException(409,'refund_provider_changed')):
            r=self.request('POST',path,json=self.body(1000))
        with topups.connect(self.dsn) as conn:
            conn.execute("UPDATE campaign_refunds SET first_attempt_at=now()-interval '2 days' WHERE id=%s",(r['id'],))
            p=conn.execute('SELECT * FROM payment_attempts WHERE campaign_id=%s',(cid,)).fetchone()
        with patch('app.refunds.current',return_value={'shop':'shop','test_mode':True}),patch('app.refunds.validate_sbp_identity'),patch('app.refunds.sbp.get_sbp_payment',return_value={'status':'succeeded','paid':True}),patch('app.refunds.sbp._request') as transport:
            result=self.request('POST',path+'/'+r['id']+'/retry');transport.assert_not_called()
        self.assertEqual(result['error_code'],'refund_manual_review_required');self.assertEqual(self.request('GET',path)['refunded'],0)


    def test_concurrent_processing_same_intent_credits_once(self):
        cid=self.paid()
        with topups.connect(self.dsn) as conn:
            rid=refunds.prepare(conn,cid,RefundInput(**self.body(8000)),self.admin)['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:refunds.process(self.dsn,rid),range(2)))
        self.assertEqual([x['status'] for x in results],['succeeded','succeeded'])
        self.assertEqual(self.balances()['RUB'],3000)
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) n FROM audit_events WHERE campaign_id=%s AND event_type='refund_succeeded'",(cid,)).fetchone()['n'],1)

    def test_blocked_admin_cannot_request_or_use_legacy_refund(self):
        cid=self.paid()
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE users SET blocked_at=now() WHERE id=%s',(self.admin,))
        self.request('POST','/admin/campaigns/'+str(cid)+'/refunds',403,json=self.body())
        with topups.connect(self.dsn) as conn:
            with self.assertRaises(HTTPException) as caught:refunds.legacy_wallet_refund(conn,cid,self.admin,'Возврат клиенту')
            self.assertEqual(caught.exception.status_code,403)
        self.assertEqual(self.balances()['RUB'],0)

    def test_request_snapshot_and_explicit_full_amount_cannot_change(self):
        cid=self.paid('sbp');body=self.body()
        with topups.connect(self.dsn) as conn:r=refunds.prepare(conn,cid,RefundInput(**body),self.admin)
        self.request('POST','/admin/campaigns/'+str(cid)+'/refunds',409,json={**body,'amount':30000})
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE payment_attempts SET amount=29900 WHERE campaign_id=%s',(cid,))
        with patch('app.refunds.sbp_refund') as network:result=refunds.process(self.dsn,r['id'])
        network.assert_not_called();self.assertEqual(result['status'],'review')
        self.assertEqual(result['error_code'],'refund_payment_ambiguous')

    def test_unsettled_provider_payment_never_becomes_manual_refund(self):
        cid=self.paid('manual')
        with topups.connect(self.dsn) as conn:
            conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,currency,amount,status) VALUES(%s,'sbp','external_web','RUB',30000,'pending')",(cid,))
        self.request('POST','/admin/campaigns/'+str(cid)+'/refunds',409,json=self.body(manual_confirmed=True))

    def test_database_blocks_rescheduling_during_and_after_refund(self):
        import psycopg
        cid=self.paid()
        with topups.connect(self.dsn) as conn:
            node=conn.execute("INSERT INTO proxy_nodes(name,host,port,status) VALUES('Refund guard',%s,18443,'active') RETURNING id",('refund-'+uuid4().hex+'.example',)).fetchone()['id']
            r=refunds.prepare(conn,cid,RefundInput(**self.body(100)),self.admin)
        def reschedule():
            with topups.connect(self.dsn) as conn:
                conn.execute("UPDATE campaigns SET proxy_node_id=%s,placement_status='scheduled',scheduled_start_at=now()+interval '1 day',scheduled_end_at=now()+interval '2 days' WHERE id=%s",(node,cid))
        with self.assertRaises(psycopg.errors.CheckViolation):reschedule()
        refunds.process(self.dsn,r['id'])
        with self.assertRaises(psycopg.errors.CheckViolation):reschedule()

    def test_partial_refund_reduces_current_cash_rank_only(self):
        from app import hall_of_fame as hall
        cid=self.paid();start=datetime.now(timezone.utc)-timedelta(days=1);end=start+timedelta(days=2)
        def score():
            with topups.connect(self.dsn) as conn:
                return next(x['score'] for x in hall.scores(conn,'spend',start,end,100,self.owner) if x['user_id']==self.owner)
        self.assertEqual(score(),25000)
        self.request('POST','/admin/campaigns/'+str(cid)+'/refunds',json=self.body(7000))
        self.assertEqual(score(),23000)  # 5000 бонусов + 2000 денег возвращены раздельно.

    def test_sbp_lost_response_then_get_reconciliation_cannot_pay_twice(self):
        cid=self.paid('sbp');path='/admin/campaigns/'+str(cid)+'/refunds'
        with topups.connect(self.dsn) as conn:p=conn.execute('SELECT * FROM payment_attempts WHERE campaign_id=%s',(cid,)).fetchone()
        paid={'id':p['provider_payment_id'],'test':True,'status':'succeeded','paid':True,'payment_method':{'type':'sbp'},'metadata':{'campaign_id':str(cid)},'amount':{'value':'300.00','currency':'RUB'},'refunded_amount':{'value':'0.00','currency':'RUB'}}
        result={'id':'refund-'+uuid4().hex,'payment_id':p['provider_payment_id'],'amount':{'value':'10.00','currency':'RUB'},'status':'succeeded'}
        with patch('app.refunds.current',return_value={'shop':'shop','test_mode':True}),patch('app.refunds.sbp.get_sbp_payment',return_value=paid),patch('app.refunds.sbp._request',side_effect=refunds.sbp.SBPProviderError('timeout')):
            first=self.request('POST',path,json=self.body(1000))
        self.assertEqual(first['status'],'review');self.request('POST',path,409,json=self.body(1000))
        paid['refunded_amount']['value']='10.00'
        with patch('app.refunds.current',return_value={'shop':'shop','test_mode':True}),patch('app.refunds.sbp.get_sbp_payment',return_value=paid),patch('app.refunds.sbp._request',return_value=result) as network:
            checked=self.request('POST',path+'/'+first['id']+'/retry',json={'provider_refund_id':result['id']})
            self.assertEqual(checked['status'],'succeeded')
            self.assertTrue(all(c.args[0]=='GET' for c in network.call_args_list))
            self.request('POST',path+'/'+first['id']+'/retry')
        self.assertEqual(self.request('GET',path)['refunded'],1000)

    def test_sbp_wrong_reconciliation_id_is_not_saved(self):
        cid=self.paid('sbp');path='/admin/campaigns/'+str(cid)+'/refunds'
        with patch('app.refunds.sbp_refund',side_effect=refunds.sbp.SBPProviderError('timeout')):
            first=self.request('POST',path,json=self.body(1000))
            self.request('POST',path+'/'+first['id']+'/retry',409,json={'provider_refund_id':'wrong-id'})
        with topups.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute('SELECT provider_refund_id FROM campaign_refunds WHERE id=%s',(first['id'],)).fetchone()['provider_refund_id'])

    def test_partial_sbp_receipt_requires_reconciliation_not_unreceipted_post(self):
        cid=self.paid('sbp')
        with topups.connect(self.dsn) as conn:
            p=conn.execute('SELECT * FROM payment_attempts WHERE campaign_id=%s',(cid,)).fetchone()
            r=refunds.prepare(conn,cid,RefundInput(**self.body(1000)),self.admin)
        with patch('app.refunds.current',return_value={'shop':'shop','test_mode':True}),patch('app.refunds.validate_sbp_identity'),patch('app.refunds.sbp.get_sbp_payment',return_value={'status':'succeeded','paid':True,'receipt_registration':'succeeded'}),patch('app.refunds.sbp._request') as network:
            result=refunds.process(self.dsn,r['id'])
        network.assert_not_called();self.assertEqual(result['error_code'],'refund_receipt_required')

    def test_worker_continues_after_one_broken_intent(self):
        with topups.connect(self.dsn) as conn:conn.execute("UPDATE campaign_refunds SET status='review' WHERE status IN ('created','pending')")
        for _ in range(2):
            cid=self.paid('manual')
            with topups.connect(self.dsn) as conn:refunds.prepare(conn,cid,RefundInput(**self.body(100,manual_confirmed=True)),self.admin)
        with patch('app.refunds.process',side_effect=[HTTPException(409,'changed'),{'status':'succeeded'}]) as process:
            self.assertEqual(refunds.service_once(self.dsn),{'checked':2,'failed':1})
            self.assertEqual(process.call_count,2)

    def test_payment_becoming_ambiguous_stays_in_review_without_provider_call(self):
        cid=self.paid('manual')
        with topups.connect(self.dsn) as conn:
            r=refunds.prepare(conn,cid,RefundInput(**self.body(100,manual_confirmed=True)),self.admin)
            conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,currency,amount,status) VALUES(%s,'sbp','external_web','RUB',30000,'pending')",(cid,))
        with patch('app.refunds.sbp_refund') as network:
            result=refunds.process(self.dsn,r['id'])
        network.assert_not_called()
        self.assertEqual((result['status'],result['error_code']),('review','refund_payment_ambiguous'))
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT refunded_amount FROM campaigns WHERE id=%s',(cid,)).fetchone()['refunded_amount'],0)


class RefundTransports(unittest.TestCase):
    def test_stars_identity_and_authoritative_result(self):
        payment={'telegram_test_environment':False,'stars_invoice_bot_id':123,'payer_telegram_user_id':456,'provider_payment_id':'charge-test'}
        from io import BytesIO
        from urllib.error import HTTPError
        with patch('app.refunds.is_test_environment',return_value=False),patch('app.refunds.credentials',return_value=('123:fake',123)),patch('app.refunds.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.status=200
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"ok":true,"result":true}'
            self.assertEqual(refunds.stars_refund(payment),'succeeded')
            import json
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(req.data),{'user_id':456,'telegram_payment_charge_id':'charge-test'})
            opener.return_value.open.side_effect=HTTPError(req.full_url,400,'Bad request',{},BytesIO(b'{"ok":false,"error_code":400,"description":"Bad Request: PAYMENT_ALREADY_REFUNDED"}'))
            self.assertEqual(refunds.stars_refund(payment),'succeeded')
            opener.return_value.open.side_effect=HTTPError(req.full_url,500,'Server error',{},BytesIO(b'{"ok":true,"result":true}'))
            with self.assertRaises(refunds.sbp.SBPProviderError):refunds.stars_refund(payment)
            with self.assertRaises(HTTPException):refunds.stars_refund({**payment,'stars_invoice_bot_id':999})

    def test_stars_network_failure_or_untrusted_body_never_succeeds(self):
        payment={'telegram_test_environment':False,'stars_invoice_bot_id':123,'payer_telegram_user_id':456,'provider_payment_id':'charge-test'}
        with patch('app.refunds.is_test_environment',return_value=False),patch('app.refunds.credentials',return_value=('123:fake',123)),patch('app.refunds.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError()
            with self.assertRaises(refunds.sbp.SBPProviderError):refunds.stars_refund(payment)
            opener.return_value.open.side_effect=None
            opener.return_value.open.return_value.__enter__.return_value.status=200
            for raw in (b'{"ok":true,"result":false}',b'[]',b'not-json',b'x'*16385):
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=raw
                with self.assertRaises(refunds.sbp.SBPProviderError):refunds.stars_refund(payment)
