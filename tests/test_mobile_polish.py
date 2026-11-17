"""История прямых оплат, удаление счетов и поиск каналов без реальных платежей."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import wallet_topups as topups, channel_directory, channel_logos, public_channels
from bot.button_styles import with_button_styles


class ChannelLookup(unittest.TestCase):
    def setUp(self):
        public_channels._cache.clear()

    def test_public_channel_and_alias_without_private_tokens(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'12345:fixture'}), patch.object(public_channels, '_telegram_chat', return_value={'id':-100123,'type':'channel','username':'MainChannel','active_usernames':['OtherChannel'],'title':'Мой канал'}) as call:
            data=channel_directory.lookup('@OtherChannel')
            self.assertEqual(data, {'title':'Мой канал','username':'otherchannel','telegram_url':'https://t.me/otherchannel','telegram_channel_id':-100123,'description':''})
            self.assertEqual(channel_directory.lookup('https://t.me/OtherChannel/'),data)
            call.assert_called_once_with('otherchannel')

    def test_invalid_reference_never_fetches_arbitrary_address(self):
        with patch.object(public_channels, '_telegram_chat') as call:
            for value in ('http://127.0.0.1','https://evil.test/channel','https://t.me/+invite','https://t.me/channel?x=1','https://t.me/channel/45'):
                with self.assertRaises(HTTPException) as error:channel_directory.lookup(value)
                self.assertEqual(error.exception.status_code,422)
            call.assert_not_called()

    def test_group_wrong_username_and_unavailable_bot(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'12345:fixture'}):
            for chat in ({'type':'supergroup','username':'example'},{'type':'channel','username':'different'},None):
                with patch.object(public_channels, '_telegram_chat', return_value=chat):
                    with self.assertRaises(HTTPException) as error:channel_directory.lookup('@example')
                    self.assertIn(error.exception.detail,('channel_lookup_unavailable','channel_not_broadcast','channel_public_unavailable'))
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':''}):
            with self.assertRaises(HTTPException) as error:channel_directory.lookup('@example')
            self.assertEqual(error.exception.status_code,503)

    def test_bot_action_colors_include_webapp_advertising_and_help(self):
        payload={'reply_markup':{'inline_keyboard':[[
            {'text':'Разместить рекламу','web_app':{'url':'https://ads.example/miniapp#/create'}},
            {'text':'Помощь','callback_data':'help'},
            {'text':'Разместить рекламу','callback_data':'advertise'}]]}}
        result=with_button_styles(payload)['reply_markup']['inline_keyboard'][0]
        self.assertEqual([b['style'] for b in result],['success','primary','success'])
        self.assertEqual(result[0]['web_app'],payload['reply_markup']['inline_keyboard'][0][0]['web_app'])
        self.assertNotIn('style',payload['reply_markup']['inline_keyboard'][0][0])


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class MobilePolishIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    setUp=wallet_fixture.WalletIntegration.setUp
    request=fixture.PostgresIntegration.request
    campaign=wallet_fixture.WalletIntegration.campaign
    star_topup=wallet_fixture.WalletIntegration.star_topup
    balances=wallet_fixture.WalletIntegration.balances
    credit=wallet_fixture.WalletIntegration.credit
    charge=wallet_fixture.WalletIntegration.charge

    def attempt(self,campaign,provider='telegram_stars',status='succeeded',test=False):
        with topups.connect(self.dsn) as conn:
            return conn.execute('''INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,telegram_test_environment,sbp_test_mode,provider_payment_id,settled_at)
                VALUES(%s,%s,%s,151,%s,%s,%s,%s,%s,now()) RETURNING id''',(campaign,provider,'telegram_bot' if provider=='telegram_stars' else 'external_web','XTR' if provider=='telegram_stars' else 'RUB',status,test if provider=='telegram_stars' else False,test if provider=='sbp' else None,str(uuid4()))).fetchone()['id']

    def test_history_includes_direct_payments_and_archives_but_not_pending_or_test(self):
        self.credit(amount=2500)
        stars=self.campaign();self.attempt(stars)
        sbp=self.campaign();self.attempt(sbp,'sbp')
        for provider in ('telegram_stars','sbp'):
            self.attempt(self.campaign(),provider,test=True)
            self.attempt(self.campaign(),provider,status='pending')
        with topups.connect(self.dsn) as conn:
            conn.execute('UPDATE campaigns SET archived_at=now() WHERE id=%s',(stars,))
        data=self.request('GET','/wallet/entries')
        self.assertEqual(data['total'],3)
        direct=[row for row in data['entries'] if row['source']=='direct']
        self.assertCountEqual([row['campaign_id'] for row in direct],[str(stars),str(sbp)])
        self.assertTrue(all(row['balance_after'] is None and row['delta']==-151 for row in direct))
        self.assertEqual(self.balances()['RUB'],2500)
        self.assertEqual(self.request('GET','/wallet/entries?page=10')['entries'],[])
        self.assertEqual(self.request('GET','/wallet/entries?page=10')['total'],3)
        self.actor={'id':self.other,'role':'advertiser'}
        self.assertEqual(self.request('GET','/wallet/entries')['total'],0)

    def test_wallet_payment_is_not_duplicated_by_direct_attempt(self):
        self.credit();cid=self.campaign();self.charge(cid)
        self.attempt(cid)
        data=self.request('GET','/wallet/entries')
        self.assertEqual(data['total'],2)
        self.assertTrue(all(row['source']=='wallet' for row in data['entries']))

    def test_terminal_invoice_hide_restore_ownership_and_late_receipt(self):
        row,pre,receipt=self.star_topup();path='/wallet/topups/'+str(row['id'])
        self.request('DELETE',path,409)
        self.assertTrue(topups.precheckout(self.dsn,pre)['ok'])
        with topups.connect(self.dsn) as conn:
            conn.execute("UPDATE wallet_topups SET status='failed' WHERE id=%s",(row['id'],))
        owner=self.actor;self.actor={'id':self.other,'role':'advertiser'}
        self.request('DELETE',path,404);self.request('POST',path+'/restore',404);self.actor=owner
        for _ in range(2):self.request('DELETE',path)
        self.assertEqual(self.request('GET','/wallet')['topups'],[])
        self.assertEqual(len(self.request('GET','/wallet?deleted_topups=true')['topups']),1)
        self.assertEqual(self.request('GET','/wallet/entries')['total'],0)
        # Закрытый до подтверждения счёт остаётся в диагностике, без начисления.
        self.assertEqual(topups.settle_stars(self.dsn,receipt)['status'],'requires_review')
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT handling_status FROM stars_payment_receipts WHERE telegram_payment_charge_id=%s',(receipt.telegram_payment_charge_id,)).fetchone()['handling_status'],'requires_review')
        self.assertEqual(self.balances()['XTR'],0)
        self.request('POST',path+'/restore')
        self.assertEqual(len(self.request('GET','/wallet')['topups']),1)
        paid,pre,receipt=self.star_topup();path='/wallet/topups/'+str(paid['id'])
        self.assertTrue(topups.precheckout(self.dsn,pre)['ok'])
        self.assertEqual(topups.settle_stars(self.dsn,receipt)['status'],'applied')
        self.request('DELETE',path)
        self.assertEqual(topups.settle_stars(self.dsn,receipt)['status'],'already_processed')
        self.assertEqual(self.balances()['XTR'],300)
        self.assertEqual(self.request('GET','/wallet/entries')['total'],1)
        topups.refund_stars(self.dsn,receipt)
        self.assertEqual(self.balances()['XTR'],0)
        self.assertEqual(self.request('GET','/wallet?deleted_topups=true')['topups'][0]['status'],'refunded')

    def test_channel_report_owner_staff_and_foreign_user(self):
        self.campaign();path='/channels/'+str(self.channel)+'/report'
        self.assertEqual(self.request('GET',path)['totals']['total'],1)
        self.actor={'id':self.other,'role':'advertiser'};self.request('GET',path,404)
        self.actor={'id':self.admin,'role':'admin'}
        self.assertEqual(len(self.request('GET',path)['campaigns']),1)
        self.request('GET','/channels/'+str(uuid4())+'/report',404)

    def test_channel_lookup_rate_limit(self):
        channel_directory._requests.clear()
        with patch.object(channel_directory,'lookup',return_value={'title':'Test'}):
            for _ in range(30):self.request('GET','/channel-preview?query=@example')
            self.request('GET','/channel-preview?query=@example',429)
        channel_directory._requests.clear()
