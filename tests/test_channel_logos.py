"""Real image decoding, bounded cache and authorization of channel logos."""
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from app import channel_logos as logos

class ChannelLogos(unittest.TestCase):
    def setUp(self):
        logos._cache.clear()
        self.env=patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'12345:secret_test','PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0'})
        self.env.start();self.addCleanup(self.env.stop)
        out=io.BytesIO();Image.new('RGB',(320,320),'orange').save(out,format='PNG');self.photo=out.getvalue()
    def test_safe_image_and_cache(self):
        chat={'type':'channel','username':'Example','photo':{'small_file_id':'file'}}
        with patch.object(logos,'_call',side_effect=[chat,{'file_path':'photos/file_1.png'}]) as call,patch.object(logos,'_read',return_value=self.photo) as read:
            result=logos.logo('https://t.me/example')
            self.assertEqual(result,logos.logo('https://t.me/example'))
            self.assertEqual(call.call_count,2)
            self.assertEqual(read.call_args.args[0].full_url,'https://api.telegram.org/file/bot12345:secret_test/photos/file_1.png')
            with Image.open(io.BytesIO(result)) as image:
                self.assertEqual(image.format,'JPEG');self.assertEqual(image.size,(160,160));self.assertFalse(image.getexif())
    def test_no_arbitrary_urls_and_no_redirects(self):
        with patch.object(logos,'_download') as download:
            for value in ('http://127.0.0.1','https://evil.test/x','https://t.me/a?x=1','https://t.me/../private'):
                self.assertIsNone(logos.logo(value))
            download.assert_not_called()
        self.assertIsNone(logos.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test'))
    def test_bad_file_path_wrong_chat_and_corrupt_image(self):
        chat={'type':'channel','username':'example','photo':{'small_file_id':'file'}}
        for path in ('../secret.png','/tmp/x.jpg','https://evil.test/x.jpg','photos/a%2f.jpg'):
            with patch.object(logos,'_call',side_effect=[chat,{'file_path':path}]),patch.object(logos,'_read') as read:
                self.assertIsNone(logos._download('12345:secret_test','example'));read.assert_not_called()
        with patch.object(logos,'_call',return_value={**chat,'username':'different'}):
            self.assertIsNone(logos._download('12345:secret_test','example'))
        with patch.object(logos,'_call',side_effect=[chat,{'file_path':'photos/a.jpg'}]),patch.object(logos,'_read',return_value=b'<svg/>'):
            self.assertIsNone(logos.logo('https://t.me/example'))
    def test_failure_cache_expiry_token_change_and_bound(self):
        with patch.object(logos,'_download',return_value=None) as download:
            self.assertIsNone(logos.logo('https://t.me/example'));logos.logo('https://t.me/example');self.assertEqual(download.call_count,1)
            with patch.object(logos.time,'monotonic',return_value=10**12):logos.logo('https://t.me/example')
            self.assertEqual(download.call_count,2)
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'99999:new_token'}):logos.logo('https://t.me/example')
            self.assertEqual(download.call_count,3)
            for i in range(140):logos.logo('https://t.me/channel_'+str(i))
            self.assertLessEqual(len(logos._cache),logos.MAX_CACHE)
    def test_auth_owner_operator_and_missing_photo(self):
        owner,other,cid=uuid4(),uuid4(),uuid4();self.actor={'id':other,'role':'advertiser'}
        def user():
            if self.actor is None:raise HTTPException(401,'unauthorized')
            return self.actor
        app=FastAPI();app.include_router(logos.router_for(lambda:'dsn',user));client=TestClient(app)
        db=MagicMock();db.__enter__.return_value=db;db.execute.return_value.fetchone.return_value={'owner_user_id':owner,'telegram_url':'https://t.me/example'}
        with patch.object(logos.psycopg,'connect',return_value=db),patch.object(logos,'logo',return_value=b'jpeg') as download:
            self.assertEqual(client.get('/api/v1/channels/'+str(cid)+'/logo').status_code,404);download.assert_not_called()
            self.actor=None;self.assertEqual(client.get('/api/v1/channels/'+str(cid)+'/logo').status_code,401)
            for actor in ({'id':owner,'role':'advertiser'},{'id':other,'role':'operator'}):
                self.actor=actor;r=client.get('/api/v1/channels/'+str(cid)+'/logo');self.assertEqual(r.status_code,200);self.assertEqual(r.headers['cache-control'],'private, no-store');self.assertNotIn('secret',r.text)
            download.return_value=None;self.assertEqual(client.get('/api/v1/channels/'+str(cid)+'/logo').status_code,204)
