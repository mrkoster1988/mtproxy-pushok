"""Контракт: приветствие + текущий экран; перезапуск, счета и ошибки уборки."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from bot.navigation import Navigation


def update(command='start', mid=1000, user=77):
    return {'message': {'message_id': mid, 'chat': {'id': user, 'type': 'private'},
                        'from': {'id': user}, 'text': '/'+command}}


class Telegram:
    def __init__(self):
        self.next = 1
        self.messages = {}
        self.calls = []
        self.fail_send = False
        self.fail_delete = False

    def __call__(self, token, method, payload, **kwargs):
        self.calls.append((method, payload.copy()))
        if method in ('sendMessage', 'sendInvoice'):
            if self.fail_send:
                raise OSError('temporary')
            mid = self.next
            self.next += 1
            self.messages[mid] = (method, payload.copy())
            return {'message_id': mid}
        mid = payload['message_id']
        if method == 'deleteMessage':
            if self.fail_delete:
                raise OSError('temporary')
            self.messages.pop(mid, None)
            return True
        if method == 'editMessageText':
            if mid not in self.messages:
                raise HTTPError('redacted', 400, '', {}, io.BytesIO(b'{"description":"message to edit not found"}'))
            if self.messages[mid][0] == 'sendInvoice':
                raise AssertionError('Invoice must not be edited as text')
            data = dict(payload)
            data.pop('message_id')
            if self.messages[mid][1] == data:
                raise HTTPError('redacted', 400, '', {}, io.BytesIO(b'{"description":"message is not modified"}'))
            self.messages[mid] = ('sendMessage', data)
            return {'message_id': mid}
        raise AssertionError(method)


class BotNavigationTests(unittest.TestCase):
    def setUp(self):
        self.api = Telegram()
        self.nav = Navigation(self.api)
        self.home = {'chat_id':77,'text':'Добро пожаловать','reply_markup':{'inline_keyboard':[]}}

    def test_start_keeps_welcome_and_always_responds_at_bottom(self):
        self.nav.deliver('token', update(), self.home)
        old = self.nav.state(77)
        self.nav.deliver('token', update(mid=1001), self.home)
        new = self.nav.state(77)
        self.assertEqual(new['welcome'], old['welcome'])
        self.assertNotEqual(new['current'], old['current'])
        self.assertEqual(set(self.api.messages), {new['welcome'],new['current']})
        deleted = [p['message_id'] for m,p in self.api.calls if m=='deleteMessage']
        self.assertIn(1001, deleted)
        self.assertNotIn(new['welcome'], deleted)

    def test_all_regular_screens_reuse_current_including_campaign_and_review(self):
        self.nav.deliver('token', update(), self.home)
        for action in ('campaigns','advertise','moderation','submit:abc','review:cancel','help'):
            self.nav.deliver('token', {'callback_query':{'data':action}}, dict(self.home,text=action))
        state=self.nav.state(77)
        self.assertEqual(len(self.api.messages),2)
        self.assertEqual(self.api.messages[state['current']][1]['text'],'help')

    def test_state_survives_restart_and_failed_cleanup_retries_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'navigation.sqlite'
            nav=Navigation(self.api,path)
            nav.deliver('token',update(),self.home)
            welcome=nav.state(77)['welcome']
            self.api.fail_delete=True
            nav.deliver('token',update(mid=1001),self.home)
            current=nav.state(77)['current']
            nav.db.close()
            nav=Navigation(self.api,path)
            self.api.fail_delete=False
            before=self.api.next
            nav.cleanup_pending('token')
            self.assertEqual(self.api.next,before)
            self.assertEqual(set(self.api.messages),{welcome,current})
            self.assertEqual(nav.state(77)['garbage'],[])
            nav.db.close()

    def test_failed_new_screen_keeps_previous_screen(self):
        self.nav.deliver('token',update(),self.home)
        previous=self.nav.state(77)
        self.api.fail_send=True
        with self.assertRaises(OSError):self.nav.deliver('token',update(mid=1002),self.home)
        self.assertEqual(self.nav.state(77),previous)
        self.assertIn(previous['current'],self.api.messages)

    def test_invoice_is_never_text_edited_and_request_amount_is_unchanged(self):
        self.nav.deliver('token',update(),self.home)
        invoice={'chat_id':77,'currency':'XTR','prices':[{'label':'Реклама','amount':37}]}
        result=self.nav.managed_call('token','sendInvoice',invoice)
        self.assertEqual(self.api.messages[result['message_id']],('sendInvoice',invoice))
        self.nav.deliver('token',update('campaigns'),dict(self.home,text='Ваши заявки'))
        state=self.nav.state(77)
        self.assertEqual(set(self.api.messages),{state['welcome'],state['current']})
        self.assertFalse(any(m=='editMessageText' and p.get('message_id')==result['message_id'] for m,p in self.api.calls))

    def test_unknown_history_and_foreign_chat_ids_are_not_deleted(self):
        self.api.messages[999]=('sendMessage',{'text':'unknown old message'})
        self.nav.deliver('123:token',update(),self.home)
        forged=update(mid=999,user=8)
        forged['message']['chat']['id']=77
        self.nav.incoming('123:token',forged)
        self.assertIn(999,self.api.messages)

    def test_deleted_welcome_is_recreated_on_start(self):
        self.nav.deliver('token',update(),self.home)
        old=self.nav.state(77)['welcome']
        del self.api.messages[old]
        self.nav.deliver('token',update(mid=1003),self.home)
        state=self.nav.state(77)
        self.assertNotEqual(old,state['welcome'])
        self.assertEqual(set(self.api.messages),{state['welcome'],state['current']})

    def test_transient_notification_uses_same_current_screen(self):
        self.nav.deliver('token',update(),self.home)
        self.nav.managed_call('token','sendMessage',{'chat_id':77,'text':'Оплата подтверждена'})
        self.nav.managed_call('token','sendMessage',{'chat_id':77,'text':'Заявка одобрена'})
        self.assertEqual(len(self.api.messages),2)
        self.assertEqual(self.api.messages[self.nav.state(77)['current']][1]['text'],'Заявка одобрена')


class RawTelegramNavigationErrors(unittest.TestCase):
    def test_http_error_body_survives_icon_fallback_inspection(self):
        from bot.main import raw_telegram_call
        for description in ('message is not modified', 'message to edit not found'):
            body = ('{"ok":false,"description":"'+description+'"}').encode()
            error = HTTPError('redacted',400,'Bad Request',{},io.BytesIO(body))
            with patch('bot.main.urlopen',side_effect=error):
                with self.assertRaises(HTTPError) as caught:
                    raw_telegram_call('token','editMessageText',{'chat_id':77,'message_id':1,'text':'menu'})
            self.assertEqual(Navigation.description(caught.exception),description)
