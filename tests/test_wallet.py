"""Денежные инварианты: гонки, повторные callback, отказ провайдера, возвраты."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4
import psycopg
from fastapi import HTTPException
import test_postgres as fixture
from app import wallet_ledger as ledger, wallet_topups as topups, wallet_worker
from app.wallet import Topup, Adjustment, Preferences, csv_cell
from app.bot_stars import StarsPrecheckoutInput, StarsReceiptInput
from app.sbp_verification import SBPVerificationError
main=fixture.main


class WalletContracts(unittest.TestCase):
    def test_amount_and_limit_validation(self):
        for value in (True,1.5,0,-1,100000001):
            with self.assertRaises(ValueError):Topup(request_id=uuid4(),currency='RUB',amount=value)
        with self.assertRaises(ValueError):Preferences(auto_pay=True,currency='RUB',per_campaign_limit=0)
        with self.assertRaises(ValueError):Adjustment(request_id=uuid4(),currency='RUB',amount=100,direction='credit',reason='   ')
        with self.assertRaises(ValueError):Topup(request_id=uuid4(),currency='BONUS',amount=100)

    def test_refund_amount_precision_and_csv(self):
        self.assertEqual(topups.refund_amount({'refunded_amount':{'value':'1.01','currency':'RUB'}},200),101)
        for value in ('-1.00','2.01','NaN','1.001','1e0'):
            with self.assertRaises(SBPVerificationError):topups.refund_amount({'refunded_amount':{'value':value,'currency':'RUB'}},200)
        for value in ('=HYPERLINK("x")','  +SUM(1)','@user','-cmd'):
            self.assertTrue(csv_cell(value).startswith("'"))
        self.assertEqual(csv_cell('Основание'),'Основание')

    def test_bot_wallet_receipt_dispatch_and_retry(self):
        from bot.main import process_stars_update
        handler=Mock(return_value={'status':'applied'});send=Mock()
        paid={'currency':'XTR','total_amount':20,'invoice_payload':'wallet:'+str(uuid4()),'telegram_payment_charge_id':'charge'}
        update={'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'successful_payment':paid}}
        self.assertTrue(process_stars_update(update,'token',handler,send))
        self.assertEqual(handler.call_args.args[0],'wallet/settle');self.assertIn('Баланс',send.call_args.args[2]['text'])
        handler.side_effect=RuntimeError('database_unavailable')
        with self.assertRaises(RuntimeError):process_stars_update(update,'token',handler,send)
        handler.side_effect=None;handler.return_value={'status':'recorded'}
        update['message'].pop('successful_payment');update['message']['refunded_payment']=paid
        self.assertTrue(process_stars_update(update,'token',handler,send));self.assertEqual(handler.call_args.args[0],'wallet/refund')


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class WalletIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.admin,self.owner,self.other,self.tariff,self.channel=[uuid4() for _ in range(5)]
        self.tg=int(uuid4().hex[:12],16)
        with topups.connect(self.dsn) as conn:
            conn.execute('UPDATE wallet_preferences SET auto_pay=false')
            for uid,tg,role in ((self.admin,self.tg+1,'admin'),(self.owner,self.tg,'advertiser'),(self.other,self.tg+2,'advertiser')):
                conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',(uid,tg,role))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr,currency) VALUES(%s,'Баланс тест',86400,30000,151,'RUB')",(self.tariff,))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,telegram_url,title) VALUES(%s,%s,%s,'Канал')",(self.channel,self.owner,'https://t.me/wallet_'+uuid4().hex[:10]))
        self.actor={'id':self.owner,'role':'advertiser','login_surface':'web'}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)

    def campaign(self,*,surface='web',price=30000,xtr=151,moderation='approved'):
        with topups.connect(self.dsn) as conn:
            return conn.execute('''INSERT INTO campaigns(advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,
                price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,pricing_version,base_price_kopecks_snapshot,stars_price_snapshot,purchase_surface)
                VALUES(%s,%s,%s,'Баланс тест',86400,%s,'RUB',%s,'awaiting_payment',1,30000,%s,%s) RETURNING id''',
                (self.owner,self.channel,self.tariff,price,moderation,xtr,surface)).fetchone()['id']

    def credit(self,c='RUB',amount=30000):
        with topups.connect(self.dsn) as conn:
            return ledger.post(conn,self.owner,c,amount,'manual_credit','test:'+str(uuid4()),'Тестовое основание',actor=self.admin)

    def balances(self):
        with topups.connect(self.dsn) as conn:return ledger.accounts(conn,self.owner)

    def charge(self,cid,currency='RUB',**kw):
        with topups.connect(self.dsn) as conn:return ledger.charge(conn,cid,self.owner,currency,**kw)

    def test_manual_journal_retries_reversal_and_role(self):
        body=dict(request_id=str(uuid4()),currency='RUB',amount=12345,direction='credit',reason='Подтверждённое пополнение')
        path='/admin/wallet/users/'+str(self.tg)+'/adjust'
        self.request('POST',path,403,json=body)
        self.actor={'id':self.admin,'role':'admin'}
        first=self.request('POST',path,201,json=body);retry=self.request('POST',path,201,json=body)
        self.assertEqual(first['id'],retry['id']);self.assertEqual(self.balances()['RUB'],12345)
        self.request('POST',path,409,json={**body,'amount':12346})
        self.request('POST','/admin/wallet/entries/'+first['id']+'/reverse',json={'reason':'Исправление ошибочного ввода'})
        self.request('POST','/admin/wallet/entries/'+first['id']+'/reverse',json={'reason':'Исправление ошибочного ввода'})
        data=self.request('GET','/admin/wallet/users/'+str(self.tg));self.assertTrue(data['reconciled']);self.assertEqual(data['total'],2)
        self.assertEqual(self.balances()['RUB'],0)
        with self.assertRaises(psycopg.Error):
            with topups.connect(self.dsn) as conn:conn.execute('DELETE FROM wallet_entries WHERE id=%s',(first['id'],))
        self.actor={'id':self.owner,'role':'advertiser'};self.assertEqual(len(self.request('GET','/wallet/entries')['entries']),2)
        self.actor={'id':self.other,'role':'advertiser'};self.assertEqual(self.request('GET','/wallet/entries')['total'],0)

    def test_concurrent_campaign_charges_no_overdraft(self):
        self.credit();a,b=self.campaign(),self.campaign()
        def run(cid):
            try:return self.charge(cid)['status']
            except HTTPException as e:return e.detail
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[a,b]))
        self.assertCountEqual(results,['paid','wallet_insufficient_funds']);self.assertEqual(self.balances()['RUB'],0)

    def test_concurrent_duplicate_credit_and_payment_once(self):
        key='duplicate:'+str(uuid4())
        def credit(_):
            with topups.connect(self.dsn) as conn:return ledger.post(conn,self.owner,'RUB',30000,'manual_credit',key,'Основание',actor=self.admin)['id']
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(credit,range(2)))
        self.assertEqual(ids[0],ids[1]);cid=self.campaign()
        with ThreadPoolExecutor(max_workers=2) as pool:pays=list(pool.map(lambda _:self.charge(cid)['id'],range(2)))
        self.assertEqual(pays[0],pays[1]);self.assertEqual(self.balances()['RUB'],0)

    def test_bonus_split_and_refund_exactly_once(self):
        self.credit(amount=20000);self.credit('BONUS',15000);cid=self.campaign(price=24000,xtr=121)
        pay=self.charge(cid,expected=24000);self.assertEqual((pay['cash_amount'],pay['bonus_amount']),(9000,15000))
        self.assertEqual(self.balances()['RUB'],11000)
        for _ in range(2):
            with topups.connect(self.dsn) as conn:ledger.refund_campaign(conn,cid,self.admin,'Отмена размещения')
        self.assertEqual(self.balances(),{'RUB':20000,'BONUS':15000,'XTR':0})
        with self.assertRaises(HTTPException):self.charge(cid)

    def test_stars_separate_snapshot_telegram_restrictions(self):
        self.credit('XTR',151);self.credit('RUB',90000);cid=self.campaign(surface='telegram',xtr=121)
        with self.assertRaises(HTTPException):self.charge(cid,'RUB')
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE tariffs SET stars_price_xtr=999 WHERE id=%s',(self.tariff,))
        self.charge(cid,'XTR',expected=121);self.assertEqual(self.balances()['XTR'],30);self.assertEqual(self.balances()['RUB'],90000)
        self.actor['login_surface']='telegram'
        self.request('PUT','/wallet/preferences',409,json=dict(auto_pay=True,currency='RUB',per_campaign_limit=30000))
        self.request('POST','/wallet/topups',409,json=dict(request_id=str(uuid4()),currency='RUB',amount=10000))

    def test_external_pending_invoice_prevents_wallet_spend(self):
        self.credit();cid=self.campaign()
        with topups.connect(self.dsn) as conn:
            conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status) VALUES(%s,'sbp','external_web',30000,'RUB','created')",(cid,))
        with self.assertRaises(HTTPException):self.charge(cid)
        self.assertEqual(self.balances()['RUB'],30000)

    def test_autopay_off_limit_insufficient_and_idempotent(self):
        cid=self.campaign();pending=self.campaign(moderation='pending');self.credit()
        wallet_worker.service_once(self.dsn);self.assertEqual(self.balances()['RUB'],30000)
        self.request('PUT','/wallet/preferences',json=dict(auto_pay=True,currency='RUB',per_campaign_limit=10000))
        wallet_worker.service_once(self.dsn);self.assertEqual(self.balances()['RUB'],30000)
        self.request('PUT','/wallet/preferences',json=dict(auto_pay=True,currency='RUB',per_campaign_limit=30000))
        with topups.connect(self.dsn) as conn:conn.execute("UPDATE campaigns SET wallet_checked_at='epoch' WHERE id=%s",(cid,))
        wallet_worker.service_once(self.dsn);wallet_worker.service_once(self.dsn)
        self.assertEqual(self.balances()['RUB'],0)
        with topups.connect(self.dsn) as conn:
            self.assertTrue(conn.execute('SELECT automatic FROM wallet_payments WHERE campaign_id=%s',(cid,)).fetchone()['automatic'])
            self.assertFalse(conn.execute('SELECT 1 FROM wallet_payments WHERE campaign_id=%s',(pending,)).fetchone())

    def star_topup(self,*,test=False):
        patcher=patch('app.wallet_topups.stars_checkout.credentials',return_value=('token',12345));patcher.start();self.addCleanup(patcher.stop)
        patcher=patch('app.wallet_topups.is_test_environment',return_value=test);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch('app.wallet_topups.stars_invoices_enabled',return_value=True);patcher.start();self.addCleanup(patcher.stop)
        with topups.connect(self.dsn) as conn:
            row=conn.execute("""INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode,status,invoice_url,expires_at)
                VALUES(%s,%s,'telegram_stars','XTR',300,'12345',%s,'pending','https://t.me/$invoice',now()+interval '30 minutes') RETURNING *""",(self.owner,uuid4(),test)).fetchone()
        pre=StarsPrecheckoutInput(query_id=str(uuid4()),telegram_user_id=self.tg,invoice_payload='wallet:'+str(row['id']),currency='XTR',total_amount=300)
        receipt=StarsReceiptInput(telegram_user_id=self.tg,invoice_payload=pre.invoice_payload,currency='XTR',total_amount=300,telegram_payment_charge_id='charge-'+str(uuid4()))
        return row,pre,receipt

    def test_star_precheckout_not_credit_success_once_refund_debt(self):
        row,pre,receipt=self.star_topup();self.assertTrue(topups.precheckout(self.dsn,pre)['ok']);self.assertEqual(self.balances()['XTR'],0)
        self.assertFalse(topups.precheckout(self.dsn,pre.model_copy(update={'query_id':'another'}))['ok'])
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:topups.settle_stars(self.dsn,receipt)['status'],range(2)))
        self.assertCountEqual(results,['applied','already_processed']);self.assertEqual(self.balances()['XTR'],300)
        cid=self.campaign();self.charge(cid,'XTR')
        for _ in range(2):topups.refund_stars(self.dsn,receipt)
        self.assertEqual(self.balances()['XTR'],-151)
        self.credit();other=self.campaign()
        with self.assertRaises(HTTPException):self.charge(other,'RUB')
        self.credit('XTR',151);self.charge(other,'RUB');self.assertEqual(self.balances()['RUB'],0)

    def test_star_refund_before_payment_and_test_money(self):
        row,pre,receipt=self.star_topup();topups.refund_stars(self.dsn,receipt)
        self.assertTrue(topups.precheckout(self.dsn,pre)['ok']);topups.settle_stars(self.dsn,receipt)
        self.assertEqual(self.balances()['XTR'],0)
        with topups.connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT status FROM wallet_topups WHERE id=%s',(row['id'],)).fetchone()['status'],'refunded')
        row,pre,receipt=self.star_topup(test=True);topups.precheckout(self.dsn,pre);topups.settle_stars(self.dsn,receipt)
        self.assertEqual(self.balances()['XTR'],0)

    def test_star_wrong_payer_amount_and_untrusted_callback(self):
        row,pre,receipt=self.star_topup()
        self.assertFalse(topups.precheckout(self.dsn,pre.model_copy(update={'telegram_user_id':self.tg+2}))['ok'])
        self.assertFalse(topups.precheckout(self.dsn,pre.model_copy(update={'total_amount':301}))['ok'])
        with patch.dict(os.environ,{'BOT_INTERNAL_SECRET':'a'*64}):
            self.request('POST','/internal/bot/wallet/settle',401,json=receipt.model_dump(mode='json'))
        self.assertEqual(topups.settle_stars(self.dsn,receipt)['status'],'requires_review');self.assertEqual(self.balances()['XTR'],0)

    def sbp(self):
        cfg=dict(enabled=True,configured=True,shop='shop',host='pay.example.com',test_mode=False)
        patcher=patch('app.wallet_topups.sbp_settings',return_value=cfg);patcher.start();self.addCleanup(patcher.stop)
        data=Topup(request_id=uuid4(),currency='RUB',amount=30000)
        with patch('app.wallet_topups.sbp_provider.create_sbp_payment',return_value=dict(payment_id='pay-'+uuid4().hex,confirmation_url='https://pay.example.com/invoice')) as create:
            row=topups.create(self.dsn,self.actor,data);retry=topups.create(self.dsn,self.actor,data);self.assertEqual(row['id'],retry['id']);create.assert_called_once()
            pid=create.return_value['payment_id'];self.assertTrue(create.call_args.kwargs['wallet_topup'])
        payment=dict(id=pid,status='succeeded',paid=True,test=False,captured_at='2026-11-16T00:00:00Z',payment_method={'type':'sbp'},metadata={'wallet_topup_id':str(row['id'])},amount={'value':'300.00','currency':'RUB'})
        return row,payment

    def test_sbp_exact_verification_partial_refunds_and_duplicate(self):
        row,payment=self.sbp();self.assertEqual(self.balances()['RUB'],0)
        with patch('app.wallet_topups.sbp_provider.get_sbp_payment',return_value=payment):
            topups.verify(self.dsn,row['id'],self.owner);topups.verify(self.dsn,row['id'],self.owner)
        self.assertEqual(self.balances()['RUB'],30000)
        payment['refunded_amount']={'value':'50.00','currency':'RUB'}
        with patch('app.wallet_topups.sbp_provider.get_sbp_payment',return_value=payment):
            topups.verify(self.dsn,row['id'],self.owner);topups.verify(self.dsn,row['id'],self.owner)
        self.assertEqual(self.balances()['RUB'],25000)
        payment['refunded_amount']['value']='100.00'
        with patch('app.wallet_topups.sbp_provider.get_sbp_payment',return_value=payment):topups.verify(self.dsn,row['id'],self.owner)
        self.assertEqual(self.balances()['RUB'],20000)

    def test_sbp_mismatch_other_owner_cannot_credit(self):
        row,payment=self.sbp();payment['metadata']={'campaign_id':str(row['id'])}
        with patch('app.wallet_topups.sbp_provider.get_sbp_payment',return_value=payment):
            with self.assertRaises(SBPVerificationError):topups.verify(self.dsn,row['id'],self.owner)
            with self.assertRaises(HTTPException):topups.verify(self.dsn,row['id'],self.other)
        self.assertEqual(self.balances()['RUB'],0)

    def test_sbp_timeout_preserves_key_not_second_invoice(self):
        cfg=dict(enabled=True,configured=True,shop='shop',host='pay.example.com',test_mode=False)
        data=Topup(request_id=uuid4(),currency='RUB',amount=30000)
        with patch('app.wallet_topups.sbp_settings',return_value=cfg):
            with patch('app.wallet_topups.sbp_provider.create_sbp_payment',side_effect=topups.sbp_provider.SBPProviderError('timeout')) as failed:
                with self.assertRaises(topups.sbp_provider.SBPProviderError):topups.create(self.dsn,self.actor,data)
            with patch('app.wallet_topups.sbp_provider.create_sbp_payment',return_value=dict(payment_id='pay-'+uuid4().hex,confirmation_url='https://pay.example.com/invoice')) as retry:
                topups.create(self.dsn,self.actor,data)
            self.assertEqual(failed.call_args.kwargs['idempotence_key'],retry.call_args.kwargs['idempotence_key'])
        with topups.connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT count(*) AS n FROM wallet_topups WHERE user_id=%s',(self.owner,)).fetchone()['n'],1)

    def test_refund_rejects_started_scheduled_slot_without_agent_ack(self):
        self.credit();cid=self.campaign();self.charge(cid)
        node=uuid4()
        with topups.connect(self.dsn) as conn:
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port,status) VALUES(%s,'Wallet node','127.0.0.1',18443,'active')",(node,))
            conn.execute("UPDATE campaigns SET proxy_node_id=%s,placement_status='scheduled',scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '1 hour' WHERE id=%s",(node,cid))
        with self.assertRaises(HTTPException) as failure:
            with topups.connect(self.dsn) as conn:ledger.refund_campaign(conn,cid,self.admin,'Запрос возврата')
        self.assertEqual(failure.exception.detail,'wallet_stop_placement_first');self.assertEqual(self.balances()['RUB'],0)
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT payment_status FROM campaigns WHERE id=%s',(cid,)).fetchone()['payment_status'],'verified')
            conn.execute("UPDATE campaigns SET scheduled_start_at=now()+interval '1 hour',scheduled_end_at=now()+interval '2 hours' WHERE id=%s",(cid,))
            ledger.refund_campaign(conn,cid,self.admin,'Отмена будущего размещения')
        self.assertEqual(self.balances()['RUB'],30000)

    def test_pending_topup_has_capacity_independent_of_refund_history(self):
        with topups.connect(self.dsn) as conn:
            # Убираем другие пополнения этого тестового класса из очереди.
            conn.execute("UPDATE wallet_topups SET next_check_at=now()+interval '1 day'")
            for _ in range(5):
                conn.execute("""INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode,status,next_check_at)
                    VALUES(%s,%s,'sbp','RUB',100,'shop',false,'succeeded',now()-interval '1 day')""",(self.owner,uuid4()))
            pending=conn.execute("""INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode,status,next_check_at)
                    VALUES(%s,%s,'sbp','RUB',100,'shop',false,'pending',now()-interval '1 minute') RETURNING id""",(self.owner,uuid4())).fetchone()['id']
        with patch('app.wallet_worker.topups.verify',return_value={}) as verify:
            wallet_worker.service_once(self.dsn,limit=1)
        self.assertEqual(verify.call_args_list[0].args[1],pending)
        self.assertEqual(verify.call_count,2) # один новый платёж и отдельная историческая проверка

    def test_reconciliation_stays_consistent_during_concurrent_credit(self):
        from threading import Event
        self.actor={'id':self.admin,'role':'admin'};self.credit(amount=100)
        locked,attempted=Event(),Event();original=ledger.accounts
        def observed(conn,uid,*,lock=False):
            result=original(conn,uid,lock=lock)
            if lock and uid==self.owner and not locked.is_set():
                locked.set();self.assertTrue(attempted.wait(3))
            return result
        def concurrent_credit():
            self.assertTrue(locked.wait(3));attempted.set();self.credit(amount=200)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(concurrent_credit)
            with patch('app.wallet.ledger.accounts',side_effect=observed):
                data=self.request('GET','/admin/wallet/users/'+str(self.tg))
            future.result(timeout=5)
        self.assertTrue(data['reconciled']);self.assertEqual(data['balances']['RUB'],100)
        self.assertEqual(self.balances()['RUB'],300)


    def test_same_transaction_entries_keep_posting_order(self):
        with topups.connect(self.dsn) as conn:
            first=ledger.post(conn,self.owner,'RUB',100,'manual_credit','order1:'+str(uuid4()),'Первая операция',actor=self.admin)
            second=ledger.post(conn,self.owner,'RUB',200,'manual_credit','order2:'+str(uuid4()),'Вторая операция',actor=self.admin)
            self.assertLess(first['entry_number'],second['entry_number'])
        data=self.request('GET','/wallet/entries')
        self.assertEqual([r['balance_after'] for r in data['entries']],[300,100])
        self.assertEqual([r['id'] for r in data['entries']],[str(second['id']),str(first['id'])])
