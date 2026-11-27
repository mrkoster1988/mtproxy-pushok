"""Подготовка сертификата не отправляет сообщение и не зачисляет бонусы."""
import io
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from PIL import Image
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import gift_sharing as sharing, wallet_topups as topups


def gift(**changes):
    data=dict(id=uuid4(),code='GIFT-'+'A'*24,amount=100025,name='Внутреннее название',active=True,
              used=0,max_uses=1,recipient_telegram_id=None,deleted_at=None,
              expires_at=datetime.now(timezone.utc)+timedelta(days=30))
    return dict(data,**changes)


class CertificateContracts(unittest.TestCase):
    def test_exact_amount_image_and_limits(self):
        self.assertEqual(sharing.rubles(100025),'1 000,25 ₽')
        for amount in (1,100000,1000000):
            raw=sharing.certificate(amount)
            image=Image.open(io.BytesIO(raw))
            self.assertEqual(image.format,'JPEG');self.assertEqual(image.size,(1200,720))
            self.assertLess(len(raw),5*1024*1024)
            self.assertNotIn(b'GIFT-',raw)
        for amount in (0,-1,1000001,True,1.5):
            with self.assertRaises(ValueError):sharing.certificate(amount)

    def test_caption_terms_and_private_recipient(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_USERNAME':'pushok_test_bot'}):
            row=gift(recipient_telegram_id=12345);data=sharing.share_data(row)
        self.assertIn('1 000,25 ₽',data['text']);self.assertIn('Именной подарок',data['text'])
        self.assertIn('в веб-кабинете',data['text']);self.assertIn('мск',data['text'])
        self.assertLessEqual(len(data['text']),1024)
        self.assertNotIn('12345',data['text']);self.assertNotIn(row['name'],data['text'])
        query=parse_qs(urlsplit(data['share_url']).query)
        self.assertEqual(query['url'][0],data['url'])
        self.assertNotIn(row['code'],data['image_path'])

    def test_unavailable_gift_is_never_shared(self):
        for changes in (dict(active=False),dict(deleted_at=datetime.now(timezone.utc)),
                        dict(used=1),dict(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))):
            with self.assertRaises(HTTPException) as error:sharing.share_data(gift(**changes))
            self.assertEqual(error.exception.status_code,409)

    def test_prepared_photo_bound_to_actor_fixed_api_and_test_environment(self):
        env={'TELEGRAM_BOT_USERNAME':'pushok_test_bot','TELEGRAM_BOT_TOKEN':'123456:synthetic_test_token',
             'PUSHOK_MINIAPP_URL':'https://ads.example.org/miniapp','PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}
        response=Mock();response.read.return_value=json.dumps({'ok':True,'result':{'id':'prepared-test','expiration_date':int(time.time())+300}}).encode()
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.dict(os.environ,env),patch.object(sharing,'build_opener',return_value=opener):
            row=gift();data=sharing.share_data(row)
            self.assertEqual(sharing.prepare_message(999,row,data),'prepared-test')
        request=opener.open.call_args.args[0];payload=json.loads(request.data)
        self.assertTrue(request.full_url.endswith('/test/savePreparedInlineMessage'))
        self.assertEqual(payload['user_id'],999);self.assertEqual(payload['result']['type'],'photo')
        self.assertEqual(payload['result']['photo_url'],'https://ads.example.org'+data['image_path'])
        self.assertEqual(payload['result']['reply_markup']['inline_keyboard'][0][0]['url'],data['url'])
        self.assertNotIn('chat_id',payload)

    def test_origin_validation_and_provider_errors_sanitized(self):
        for value in ('https://evil.test/miniapp?token=x','http://ads.example.org/miniapp','https://user:pass@ads.example.org/miniapp','https://127.0.0.1/miniapp','https://ads.example.org:8443/miniapp'):
            with patch.dict(os.environ,{'PUSHOK_MINIAPP_URL':value}):
                with self.assertRaises(HTTPException):sharing.public_origin()
        opener=Mock();opener.open.side_effect=OSError('https://api.telegram.org/botSECRET')
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'123456:synthetic_test_token','PUSHOK_MINIAPP_URL':'https://ads.example.org/miniapp'}),patch.object(sharing,'build_opener',return_value=opener):
            with self.assertRaises(HTTPException) as error:sharing.prepare_message(999,gift(),{'image_path':'/image.jpg','text':'Тест','url':'https://t.me/test'})
        self.assertEqual(error.exception.detail,'gift_share_image_unavailable')


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class CertificateIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        wallet_fixture.WalletIntegration.setUp(self)
        sharing._requests.clear()
        self.bot=patch.dict(os.environ,{'TELEGRAM_BOT_USERNAME':'pushok_test_bot'});self.bot.start();self.addCleanup(self.bot.stop)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=true')
        self.actor={'id':self.admin,'role':'admin'}
        data=self.request('POST','/admin/wallet/gifts',201,json=dict(request_id=str(uuid4()),name='Сертификат',amount=100000,max_uses=1,expires_at=(datetime.now(timezone.utc)+timedelta(days=5)).isoformat()))
        self.gid=data['id'];self.path='/admin/wallet/gifts/'+self.gid+'/share'

    def test_authorized_actor_and_no_redemption_side_effect(self):
        with patch.object(sharing,'prepare_message',return_value='prepared') as prepare:
            data=self.request('POST',self.path+'?prepare=true')
            self.assertEqual(data['message_id'],'prepared');self.assertEqual(prepare.call_args.args[0],self.tg+1)
            self.actor={'id':self.owner,'role':'advertiser'};self.request('POST',self.path,403)
            self.actor={'id':self.other,'role':'operator'};self.request('POST',self.path,403)
            self.assertEqual(prepare.call_count,1)
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM wallet_gift_redemptions WHERE gift_id=%s',(self.gid,)).fetchone()['n'],0)

    def test_fallback_inactive_exhausted_disabled_wallet_and_rate_limit(self):
        with patch.object(sharing,'prepare_message',side_effect=HTTPException(503,'gift_share_image_unavailable')):
            self.assertIsNone(self.request('POST',self.path+'?prepare=true')['message_id'])
        self.request('PUT','/admin/wallet/gifts/'+self.gid,json={'active':False});self.request('POST',self.path,409)
        self.request('PUT','/admin/wallet/gifts/'+self.gid,json={'active':True})
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=false')
        self.request('POST',self.path,409)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=true')
        sharing._requests.clear()
        for _ in range(10):self.request('POST',self.path)
        self.request('POST',self.path,429)
        sharing._requests.clear()
        code=self.request('GET','/admin/wallet/gifts')['items'][0]['code']
        self.actor={'id':self.owner,'role':'advertiser'};self.request('POST','/wallet/gifts/redeem',json={'code':code})
        self.actor={'id':self.admin,'role':'admin'};self.request('POST',self.path,409)

    def test_public_image_requires_no_session_and_no_code(self):
        reply=self.client.get('/api/v1/certificates/bonus/v1/100000.jpg')
        self.assertEqual(reply.status_code,200);self.assertEqual(reply.headers['content-type'],'image/jpeg')
        self.assertEqual(Image.open(io.BytesIO(reply.content)).size,(1200,720))
        self.assertEqual(self.client.get('/api/v1/certificates/bonus/v1/1000001.jpg').status_code,422)
