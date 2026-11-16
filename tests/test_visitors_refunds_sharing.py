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

    def test_bot_colors_and_coupon_identity(self):
        from bot.main import keyboard,response_for
        from bot.button_styles import with_button_styles
        with patch('bot.main.miniapp_url',return_value='https://example.org/'),patch('bot.main.current_proxy_url',return_value=None):
            rows=with_button_styles({'reply_markup':keyboard()})['reply_markup']['inline_keyboard']
        self.assertEqual(rows[0][0]['style'],'success');self.assertEqual(rows[-1][-1]['style'],'primary')
        handler=Mock();code=uuid4().hex
        update={'message':{'text':'/start c_'+code,'from':{'id':1},'chat':{'id':1,'type':'private'}}}
        self.assertIn('Купон',response_for(update,coupon_handler=handler)[0]['text']);handler.assert_called_once_with(1,code)
        handler.reset_mock();update['message']['from']['id']=2;response_for(update,coupon_handler=handler);handler.assert_not_called()


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

    def test_visitors_identity_duplicates_search_and_role(self):
        vid=str(uuid4());self.request('POST','/visits',204,json={'visit_id':vid});self.request('POST','/visits',204,json={'visit_id':vid})
        self.request('GET','/admin/visitors',403);self.admin_actor()
        data=self.request('GET','/admin/visitors?q='+str(self.tg))
        owner=next(r for r in data['items'] if r['telegram_user_id']==self.tg)
        self.assertEqual(owner['visits'],1);self.assertNotIn('session_id',owner);self.assertNotIn('token',owner)
        self.request('GET','/admin/visitors?offset=-1',422)

    def test_coupon_transfer_single_claim_and_old_owner_cannot_spend(self):
        with topups.connect(self.dsn) as conn:
            oid=conn.execute("INSERT INTO growth_offers(kind,name,code,percent,recipient_user_id,total_limit) VALUES('promo','Личный',%s,20,%s,1) RETURNING id",(uuid4().hex,self.owner)).fetchone()['id']
        link=self.request('POST','/growth/coupons/'+str(oid)+'/share')['link'];share=link.split('c_')[1]
        self.assertEqual(self.request('POST','/growth/coupons/'+str(oid)+'/share')['link'],link)
        self.actor={'id':self.other,'role':'advertiser'};self.request('POST','/growth/coupons/'+str(oid)+'/share',404)
        with patch('app.growth._verify_bot_secret'):
            path='/internal/bot/coupon';body={'telegram_user_id':self.tg+2,'code':share}
            self.request('POST',path,json=body);self.request('POST',path,json=body)
            self.request('POST',path,409,json={'telegram_user_id':self.tg+1,'code':share})
        with topups.connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT recipient_user_id FROM growth_offers WHERE id=%s',(oid,)).fetchone()['recipient_user_id'],self.other)
        self.actor={'id':self.owner,'role':'advertiser'};self.request('POST','/growth/coupons/'+str(oid)+'/share',404)
