from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import HTTPException
from app import service_messages as service
from bot.main import legacy_entry


class ServiceMessages(TestCase):
    def test_notification_buttons_go_to_the_cabinet_identity(self):
        with patch.dict('os.environ',TELEGRAM_BOT_USERNAME='mtproxy_pushok_bot'):
            for button,expected in [
                ({'web_app':{'url':'https://pushok.shop/miniapp?vpn=renew'}},'vpn_renew'),
                ({'callback_data':'campaigns'},'campaigns'),
                ({'web_app':{'url':'https://pushok.shop/miniapp#/profile'}},'profile'),
                ({'callback_data':'unknown'},'dashboard')]:
                self.assertEqual(service.cabinet_link({**button,'text':'Открыть'}),
                    {'text':'Открыть','url':'https://t.me/mtproxy_pushok_bot?startapp='+expected})
    def test_help_and_payment_are_routed_separately(self):
        def update(text):
            return {'message':{'from':{'id':22},'chat':{'id':22,'type':'private'},'text':text}}
        self.assertEqual(legacy_entry(update('/help'))[0]['reply_markup']['inline_keyboard'][0][0]['url'],'https://pushok.shop/ai-support')
        self.assertIsNone(legacy_entry(update('/paysupport')))
        self.assertIsNone(legacy_entry(update('/start flow_ABC')))
    def test_unopened_support_chat_is_not_acknowledged(self):
        with patch.dict('os.environ',DATABASE_URL='fixture'), patch.object(service,'verified_identity',return_value=1), \
                patch.object(service.support,'credential',return_value={'token':'fixture','username':'server_pushok_bot'}), \
                patch.object(service.support,'connection') as db, patch.object(service.support,'bot_call') as call:
            db.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=None
            with self.assertRaises(HTTPException) as e:
                service.send(service.Notice(chat_id=22,text='Напоминание'))
            self.assertEqual(e.exception.detail,'support_bot_chat_not_started');call.assert_not_called()
