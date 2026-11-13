import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL','postgresql://unused')
os.environ.setdefault('SESSION_SIGNING_KEY','a'*64)
from app import main, control
from app.payment_gate import stars_invoices_enabled
from fastapi.testclient import TestClient
from bot.navigation import Navigation

class LaunchFixes(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'PUSHOK_CONTROL_DIR':self.temp.name,'PUSHOK_STARS_PAYMENTS_ENABLED':'0'})
  self.env.start();self.client=TestClient(main.app)
 def tearDown(self):
  main.app.dependency_overrides.clear();self.env.stop();self.temp.cleanup()
 def test_free_tariff_validation(self):
  args=dict(name='Бесплатно',duration_seconds=86400,price_kopecks=0,stars_price_xtr=0,is_active=True)
  self.assertIsNone(main.TariffSettingsUpdate(**args).stars_price_xtr)
  with self.assertRaises(ValueError): main.TariffSettingsUpdate(**{**args,'stars_price_xtr':5})
  with self.assertRaises(ValueError): main.TariffSettingsUpdate(**{**args,'price_kopecks':-1})
 def test_control_requires_admin_and_no_bootstrap_no_update(self):
  self.assertEqual(self.client.post('/api/v1/admin/control/update').status_code,401)
  main.app.dependency_overrides[main.get_current_user]=lambda:dict(id='u',role='advertiser')
  self.assertEqual(self.client.put('/api/v1/admin/control/payments',json={'stars':False,'sbp':False}).status_code,403)
  main.app.dependency_overrides[main.get_current_user]=lambda:dict(id='u',role='admin')
  self.assertEqual(self.client.post('/api/v1/admin/control/update').status_code,409)
  control.write('installed.json',{'version':1})
  self.assertEqual(self.client.post('/api/v1/admin/control/update').status_code,202)
  self.assertEqual(self.client.post('/api/v1/admin/control/update').status_code,409)
 def test_payment_runtime_toggle_and_missing_credentials(self):
  main.app.dependency_overrides[main.get_current_user]=lambda:dict(id='u',role='admin')
  with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'123456:abcdefghijklmnopqrstuvw','YOOKASSA_SHOP_ID':'','YOOKASSA_SECRET_KEY':''}):
   self.assertEqual(self.client.put('/api/v1/admin/control/payments',json={'stars':True,'sbp':False}).status_code,200)
   self.assertTrue(stars_invoices_enabled())
   self.assertEqual(self.client.put('/api/v1/admin/control/payments',json={'stars':True,'sbp':True}).status_code,409)
 def test_menu_edits_and_keeps_financial_messages(self):
  call=Mock(return_value={'message_id':5});nav=Navigation(call)
  nav.deliver('t',{'message':{'text':'/start'}},{'chat_id':1,'text':'home'})
  nav.deliver('t',{'callback_query':{'data':'tariffs'}},{'chat_id':1,'text':'prices'})
  self.assertEqual(call.call_args.args[1],'editMessageText')
  nav.deliver('t',{'callback_query':{'data':'submit:abc'}},{'chat_id':1,'text':'submitted'})
  self.assertEqual(call.call_args.args[1],'sendMessage')
 def test_same_menu_does_not_send_duplicate(self):
  err=HTTPError('redacted',400,'',{},io.BytesIO(b'{"description":"Bad Request: message is not modified"}'))
  call=Mock(side_effect=err);nav=Navigation(call);nav.messages[1]=5
  nav.deliver('t',{'callback_query':{'data':'tariffs'}},{'chat_id':1,'text':'prices'})
  self.assertEqual(call.call_count,1)

 def test_campaign_payment_screen_is_never_edited_or_deleted(self):
  for cached in (None, 5):
   call=Mock(return_value={'message_id':10});nav=Navigation(call)
   if cached: nav.messages[1]=cached
   update={'callback_query':{'data':'help','message':{'message_id':8,'reply_markup':{'inline_keyboard':[[{'callback_data':'tariffs'},{'callback_data':'stars:order'}]]}}}}
   nav.deliver('t',update,{'chat_id':1,'text':'help'})
   self.assertEqual(call.call_args_list[0].args[1],'sendMessage')
   for record in call.call_args_list:
    self.assertNotEqual(record.args[1],'editMessageText')
    self.assertNotEqual(record.args[2].get('message_id'),8)
