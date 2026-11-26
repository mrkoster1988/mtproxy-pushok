from unittest import TestCase
from unittest.mock import patch
import json
from io import BytesIO
from urllib.error import HTTPError
from fastapi import HTTPException
from app import service_messages as service
from bot.main import legacy_entry, notification_chat_opened


class ServiceMessages(TestCase):
    def test_rich_notice_keeps_action_history_and_falls_back_on_explicit_rejection(self):
        notice=service.Notice(chat_id=22,parse_mode='HTML',rich=True,
            text='<b>💛 Premium</b>\n\nБессрочно &amp; бесплатно\nВторой абзац\n\n<i>Команда Pushok</i>',
            reply_markup={'inline_keyboard':[[{'text':'Показать подписку','callback_data':'subscription_widget'}]]})
        message={'chat':{'id':22},'message_id':51,'date':100}
        with patch.dict('os.environ',TELEGRAM_BOT_TOKEN='20:'+'X'*30), \
             patch.object(service,'verify_sender'), patch.object(service,'is_test_environment',return_value=False), \
             patch.object(service,'telegram_call',return_value=message) as call, \
             patch('app.recipient_history.record') as record:
            self.assertEqual(service.send(notice),{'sent':True})
            self.assertEqual(call.call_args.args[1],'sendRichMessage')
            payload=call.call_args.args[2]
            self.assertIn('<h3>',payload['rich_message']['html'])
            self.assertIn('<footer>',payload['rich_message']['html'])
            self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'],'subscription_widget')
            self.assertIn('Бессрочно & бесплатно',record.call_args.args[1]['text'])
            call.reset_mock()
            call.side_effect=[HTTPException(400,'rich_unsupported'),{**message,'text':'Premium'}]
            service.send(notice)
            self.assertEqual([c.args[1] for c in call.call_args_list],['sendRichMessage','sendMessage'])
            call.reset_mock()
            call.side_effect=HTTPException(503,'service_notice_unavailable')
            with self.assertRaises(HTTPException):
                service.send(notice)
            call.assert_called_once()

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
    def test_background_messages_use_the_cabinet_token_without_a_support_contact(self):
        token = '20:'+'C'*30
        def reply(request, *_):
            method = request.full_url.split('/')[-1]
            self.assertIn('/bot'+token+'/', request.full_url)
            if method == 'getMe':
                result = {'is_bot':True,'username':'mtproxy_pushok_bot'}
            else:
                self.assertEqual(method, 'sendMessage')
                payload = json.loads(request.data)
                self.assertEqual(payload['chat_id'], 22)
                self.assertEqual(payload['text'], 'Напоминание')
                result = {'message_id': 51, 'chat': {'id':22}}
            return json.dumps({'ok':True, 'result':result}).encode()
        service.verify_sender.cache_clear()
        self.addCleanup(service.verify_sender.cache_clear)
        with patch.dict('os.environ',TELEGRAM_BOT_TOKEN=token), patch.object(service,'read_url',side_effect=reply) as call, \
             patch('app.recipient_history.record'):
            for shop in (False, True):
                self.assertEqual(service.send(service.Notice(chat_id=22,text='Напоминание'),shop), {'sent':True})
            self.assertEqual(call.call_count,3)
            self.assertIn('Открыть магазин',str(json.loads(call.call_args.args[0].data)))

    def test_wrong_bot_is_rejected_before_sending(self):
        with patch.dict('os.environ',TELEGRAM_BOT_TOKEN='42:'+'X'*32), patch.object(service,'telegram_call',
                return_value={'is_bot':True,'username':'server_pushok_bot'}) as call:
            with self.assertRaises(HTTPException) as exc:
                service.send(service.Notice(chat_id=22,text='Сообщение'))
            self.assertEqual(exc.exception.detail,'cabinet_bot_wrong_identity')
            self.assertEqual(call.call_count,1)

    def test_recipient_errors_are_distinct_from_transport_failures(self):
        for code,description,expected in [(403,"Forbidden: bot can't initiate conversation with a user",'cabinet_bot_chat_not_started'),
                (400,'Bad Request: chat not found','cabinet_bot_chat_not_started'),
                (403,'Forbidden: bot was blocked by the user','recipient_blocked'),
                (403,'Forbidden','service_notice_unavailable'),(429,'Too many requests','service_notice_unavailable')]:
            failure=HTTPError('https://example.invalid',code,'',{},BytesIO(json.dumps({'description':description}).encode()))
            with patch.object(service,'read_url',side_effect=failure), self.assertRaises(HTTPException) as exc:
                service.telegram_call('22:fixture','sendMessage',{'chat_id':22,'text':'Тариф'})
            self.assertEqual(exc.exception.detail,expected)

    def test_start_or_write_permission_resumes_only_the_private_recipient(self):
        msg={'from':{'id':22},'chat':{'id':22,'type':'private'},'text':'/start notifications'}
        with patch('bot.main._internal_api') as api:
            self.assertTrue(notification_chat_opened({'message':msg}))
            self.assertEqual([c.args for c in api.call_args_list],[
                ('vpn-reminders/resume',{'telegram_user_id':22}),('vpn-notices/resume',{'telegram_user_id':22})])
            api.reset_mock()
            self.assertFalse(notification_chat_opened({'message':{**msg,'chat':{'id':44,'type':'private'}}}))
            self.assertFalse(notification_chat_opened({'message':{**msg,'text':'/help'}}))
            api.assert_not_called()
            self.assertTrue(notification_chat_opened({'message':{**msg,'text':'','write_access_allowed':{}}}))
            self.assertEqual(api.call_count,2)
