"""Вход в одну заявку из Telegram, без создания лишнего черновика."""
import os
import unittest
from unittest.mock import Mock, patch
from bot.main import response_for
from bot.operator_inbox import OperatorInbox


class SinglePageEntryTests(unittest.TestCase):
    def test_command_and_old_callback_skip_draft_wizard(self):
        env = {'PUSHOK_MINIAPP_ENABLED': '1',
               'PUSHOK_MINIAPP_URL': 'https://pushok.example/miniapp'}
        for callback in (False, True):
            wizard = Mock()
            wizard.sessions = {77: {'phase': 'channel'}}
            message = {'chat': {'id': 77, 'type': 'private'}, 'from': {'id': 77},
                       'text': '/advertise'}
            update = {'message': message} if not callback else {'callback_query': {
                'id': 'callback', 'from': {'id': 77}, 'message': message,
                'data': 'advertise'}}
            with patch.dict(os.environ, env):
                payload, _ = response_for(update, wizard=wizard)
            wizard.handle.assert_not_called()
            self.assertNotIn(77, wizard.sessions)
            buttons = [b for row in payload['reply_markup']['inline_keyboard'] for b in row]
            self.assertEqual([b['web_app']['url'] for b in buttons if 'web_app' in b],
                             ['https://pushok.example/miniapp#/create'])
            self.assertFalse(any(b.get('callback_data') == 'advertise' for b in buttons))

    def test_operator_queue_opens_exact_campaign_without_deciding(self):
        cid = '00000000-0000-4000-8000-000000000123'
        load = Mock(return_value={'pending': [{'campaign_id': cid,
                    'channel_title': '@channel', 'channel_url': 'https://t.me/channel',
                    'price_kopecks': 0}]})
        decide = Mock()
        inbox = OperatorInbox(load, decide, Mock(),
                              miniapp_loader=lambda: 'https://pushok.example/miniapp')
        payload, _ = inbox.handle({'message': {'chat': {'id': 77, 'type': 'private'},
                                 'from': {'id': 77}, 'text': '/moderation'}})
        load.assert_called_once_with(77, subscribe=True)
        decide.assert_not_called()
        button = payload['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['web_app']['url'],
                         'https://pushok.example/miniapp#/moderation/' + cid)
        self.assertNotIn('callback_data', button)
