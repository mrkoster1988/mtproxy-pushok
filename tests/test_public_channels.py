"""Публичность: реальный HTTP-разбор, отсутствие записей при отказе, повторы и гонки."""
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

import test_postgres as fixture
import test_wallet as wallet_fixture
from fastapi import HTTPException
from app import public_channels as channels

REAL_LOOKUP = channels.lookup


def chat(name):
    return dict(id=-1001234567890, type='channel', username=name, title='Публичный канал')


class PublicChannels(unittest.TestCase):
    def setUp(self):
        channels._cache.clear()
        env = patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'12345:secret_fixture', 'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0'})
        env.start(); self.addCleanup(env.stop)

    def test_preview_cache_and_fresh_submit_check(self):
        with patch.object(channels, '_telegram_chat', side_effect=lambda name: chat(name)) as call:
            first = channels.lookup('@Example')
            self.assertEqual(first['telegram_channel_id'], -1001234567890)
            self.assertEqual(channels.lookup('https://t.me/example'), first)
            self.assertEqual(call.call_count, 1)
            channels.lookup('@example', fresh=True)
            self.assertEqual(call.call_count, 2)
            with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):channels.lookup('@example')
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'23456:other'}):channels.lookup('@example')
            self.assertEqual(call.call_count, 4)
            with patch.object(channels.time, 'monotonic', return_value=10**12):channels.lookup('@example')
            self.assertEqual(call.call_count, 5)

    def test_alias_and_numeric_identity(self):
        with patch.object(channels, '_telegram_chat', return_value={**chat('mainname'), 'active_usernames':['ExtraName']}):
            self.assertEqual(channels.lookup('@ExtraName')['username'], 'extraname')
            with self.assertRaises(HTTPException) as error:channels.lookup('@ExtraName', expected_id=-999)
            self.assertEqual(error.exception.detail, 'channel_identity_changed')

    def test_private_group_user_and_malformed_result(self):
        for result, code in [(dict(type='group'), 'channel_not_broadcast'),
                             (dict(type='supergroup'), 'channel_not_broadcast'),
                             (dict(type='private'), 'channel_not_broadcast'),
                             (dict(type='channel', id=-123), 'channel_public_unavailable'),
                             (chat('someone_else'), 'channel_public_unavailable'),
                             ({**chat('example'), 'id':True}, 'channel_lookup_unavailable'),
                             (None, 'channel_lookup_unavailable')]:
            with self.subTest(result=result), patch.object(channels, '_telegram_chat', return_value=result):
                with self.assertRaises(HTTPException) as error:channels.lookup('@example', fresh=True)
                self.assertEqual(error.exception.detail, code)
                self.assertEqual(len(channels._cache), 0)

    def test_private_links_and_external_urls_never_request_network(self):
        with patch.object(channels, '_telegram_chat') as transport:
            for value in ['https://t.me/+abcd','https://t.me/joinchat/abcd','https://t.me/channel/5','https://evil.test/channel','@ab','https://t.me/channel?x=1']:
                with self.assertRaises(HTTPException):channels.lookup(value)
            transport.assert_not_called()

    def test_transport_timeout_auth_throttle_and_not_found_are_distinct(self):
        for failure, status, code in [(TimeoutError(),503,'channel_lookup_unavailable'),
                                     (URLError('offline'),503,'channel_lookup_unavailable'),
                                     *[(HTTPError('https://secret', n,'token',{},None), s, c) for n,s,c in
                                       [(400,422,'channel_public_unavailable'),(403,422,'channel_public_unavailable'),
                                        (401,503,'channel_lookup_unavailable'),(429,429,'channel_lookup_rate_limit'),(500,503,'channel_lookup_unavailable')]]]:
            opener = MagicMock(); opener.open.side_effect = failure
            with patch.object(channels, 'build_opener', return_value=opener):
                with self.assertRaises(HTTPException) as error:channels.lookup('@example', fresh=True)
                self.assertEqual((error.exception.status_code,error.exception.detail),(status,code))
                self.assertNotIn('secret',str(error.exception.detail))
                self.assertEqual(opener.open.call_args.kwargs['timeout'],4)
        with patch.object(channels, '_telegram_chat', side_effect=lambda name: chat(name)):
            self.assertEqual(channels.lookup('@example')['username'],'example')

    def test_actual_transport_uses_environment_and_bounded_json(self):
        for environment, part in [('0','/getChat'),('1','/test/getChat')]:
            opener = MagicMock(); opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({'ok':True,'result':chat('example')}).encode()
            with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':environment}), patch.object(channels,'build_opener',return_value=opener):
                channels.lookup('@example',fresh=True)
                request=opener.open.call_args.args[0]
                self.assertTrue(request.full_url.endswith(part))
                self.assertEqual(json.loads(request.data), {'chat_id':'@example'})
                opener.open.return_value.__enter__.return_value.read.assert_called_once_with(65537)
        with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'bad'}), patch.object(channels,'_telegram_chat') as transport:
            with self.assertRaises(HTTPException):channels.lookup('@example')
            transport.assert_not_called()


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class PublicChannelIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        wallet_fixture.WalletIntegration.setUp(self)
        channels._cache.clear()
        lookup=patch.object(channels,'lookup',side_effect=REAL_LOOKUP)
        lookup.start();self.addCleanup(lookup.stop)
        transport=patch.object(channels,'_telegram_chat',side_effect=lambda name:chat(name))
        self.transport=transport.start();self.addCleanup(transport.stop)

    def payload(self):
        return dict(request_id=str(uuid4()),channel_id=str(self.channel),tariff_id=str(self.tariff),
                    expected_price_kopecks=30000,expected_duration_seconds=86400)

    def test_rejected_order_has_no_campaign_or_idempotency_record(self):
        body=self.payload()
        self.transport.side_effect=HTTPException(503,'channel_lookup_unavailable')
        self.request('POST','/orders',503,json=body)
        with channels.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM campaigns WHERE advertiser_user_id=%s',(self.owner,)).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM order_submissions WHERE user_id=%s',(self.owner,)).fetchone()[0],0)
        self.transport.side_effect=lambda name:chat(name)
        first=self.request('POST','/orders',201,json=body)
        self.transport.reset_mock();self.transport.side_effect=HTTPException(503,'channel_lookup_unavailable')
        self.assertEqual(self.request('POST','/orders',201,json=body)['id'],first['id'])
        self.transport.assert_not_called()

    def test_invalid_new_channel_and_legacy_submission_are_blocked(self):
        self.transport.side_effect=lambda name:{**chat(name),'type':'supergroup'}
        data=dict(title='Группа',telegram_url='https://t.me/not_channel')
        self.request('POST','/channels',422,json=data)
        body=self.payload();body.pop('channel_id');body['channel']=data
        self.request('POST','/orders',422,json=body)
        draft=self.request('POST','/campaigns',201,json=dict(channel_id=str(self.channel),tariff_id=str(self.tariff)))
        self.request('POST','/campaigns/'+draft['id']+'/submit',422)
        with channels.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT moderation_status FROM campaigns WHERE id=%s',(draft['id'],)).fetchone()[0],'draft')

    def test_access_precedes_network_and_changed_url_is_not_accepted(self):
        body=self.payload();body['channel_id']=str(uuid4())
        self.request('POST','/orders',404,json=body);self.transport.assert_not_called()
        def changed(name):
            # Успешный UPDATE доказывает, что сеть не выполняется под блокировкой строки.
            with channels.psycopg.connect(self.dsn) as conn:
                conn.execute("SET lock_timeout='200ms'")
                conn.execute("UPDATE sponsor_channels SET telegram_url='https://t.me/changed_channel' WHERE id=%s",(self.channel,))
            return chat(name)
        self.transport.side_effect=changed
        result=self.request('POST','/orders',409,json=self.payload())
        self.assertEqual(result['detail'],'channel_changed_during_check')

    def test_edit_revalidates_and_cannot_replace_url_during_check(self):
        path='/channels/'+str(self.channel)
        self.transport.side_effect=lambda name:{**chat(name),'type':'private'}
        self.request('PATCH',path,422,json=dict(title='Канал',telegram_url='https://t.me/new_channel'))
        self.transport.side_effect=lambda name:chat(name)
        self.assertEqual(self.request('PATCH',path,json=dict(title='Канал',telegram_url='https://t.me/new_channel'))['telegram_url'],'https://t.me/new_channel')
