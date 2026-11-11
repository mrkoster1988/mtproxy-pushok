import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from app import main, stars_checkout
from app.bot_stars import StarsPrepareOutput
from fastapi.testclient import TestClient


class StarsCheckoutTests(unittest.TestCase):
    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_auth_and_gate_precede_database_and_telegram(self):
        path='/api/v1/campaigns/'+str(uuid4())+'/stars'
        with TestClient(main.app) as client, patch.object(stars_checkout.psycopg,'connect') as db:
            self.assertEqual(client.get(path).status_code,401)
            self.assertEqual(client.post(path+'/checkout').status_code,401)
            main.app.dependency_overrides[main.get_current_user]=lambda:{'id':uuid4(),'role':'advertiser'}
            with patch.dict(os.environ,{'PUSHOK_STARS_PAYMENTS_ENABLED':'0','PUSHOK_MINIAPP_ENABLED':'1'}):
                self.assertEqual(client.post(path+'/checkout').status_code,409)
            db.assert_not_called()

    def test_transport_sends_only_fixed_xtr_price_and_validates_link(self):
        prepared=StarsPrepareOutput(invoice_payload='stars:'+str(uuid4()),stars_amount=180,title='Реклама',description='Заявка')
        stream=MagicMock();stream.__enter__.return_value=stream;stream.status=200
        opener=MagicMock();opener.open.return_value=stream
        with patch.object(stars_checkout,'build_opener',return_value=opener):
            stream.read.return_value=json.dumps({'ok':True,'result':'https://t.me/$fixture'}).encode()
            self.assertEqual(stars_checkout.create_invoice_link('12345:fixture-secret',prepared),'https://t.me/$fixture')
            request=opener.open.call_args.args[0]
            body=json.loads(request.data)
            self.assertEqual(body['currency'],'XTR')
            self.assertEqual(body['prices'],[{'label':'Спонсорство MTProxy','amount':180}])
            self.assertEqual(body['provider_token'],'')
            for link in ('http://t.me/$fixture','https://evil.example/$fixture','https://t.me/$fixture?token=secret'):
                stream.read.return_value=json.dumps({'ok':True,'result':link}).encode()
                with self.assertRaises(stars_checkout.StarsLinkError):
                    stars_checkout.create_invoice_link('12345:fixture-secret',prepared)
            opener.open.side_effect=OSError('https://api.telegram.org/botSECRET')
            with self.assertRaises(stars_checkout.StarsLinkError) as error:
                stars_checkout.create_invoice_link('12345:fixture-secret',prepared)
            self.assertNotIn('SECRET',str(error.exception))


class MiniAppButtonTests(unittest.TestCase):
    def test_launch_button_gated_and_https_url_validated(self):
        from bot.main import keyboard
        def links():
            return [button for row in keyboard()['inline_keyboard'] for button in row if 'web_app' in button]
        with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'1','PUSHOK_MINIAPP_URL':'https://ads.example.org/miniapp'}):
            self.assertEqual(links()[0]['web_app']['url'],'https://ads.example.org/miniapp')
            for url in ('http://ads.example.org/miniapp','https://user:pass@ads.example.org/miniapp',
                        'https://ads.example.org/miniapp?secret=x','https://ads.example.org/miniapp#x',
                        'https://127.0.0.1/miniapp','https://ads.example.org/other'):
                with patch.dict(os.environ,{'PUSHOK_MINIAPP_URL':url}):self.assertEqual(links(),[])
            with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'0'}):self.assertEqual(links(),[])
