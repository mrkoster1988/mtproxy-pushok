import os
import unittest
from unittest.mock import patch
from bot.main import response_for, show_subscription


class SubscriptionCommandTests(unittest.TestCase):
    @patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1', 'PUSHOK_MINIAPP_URL': 'https://pushok.example/miniapp'})
    def test_private_chat_opens_role_aware_vpn_in_miniapp(self):
        msg = {'chat': {'id': 9, 'type': 'private'}, 'from': {'id': 9}, 'text': '/subscriptions'}
        payload, _ = response_for({'message': msg})
        urls = [row[0]['web_app']['url'] for row in payload['reply_markup']['inline_keyboard']]
        self.assertEqual(urls, ['https://pushok.example/miniapp?vpn=home'])
        msg['chat']['type'] = 'group'
        self.assertIsNone(response_for({'message': msg}))

    @patch('bot.main.is_test_environment',return_value=False)
    @patch('bot.main.raw_telegram_call')
    @patch('bot.main._internal_api',return_value={'ok':True})
    def test_button_and_plural_command_open_the_private_widget(self, api, telegram, _environment):
        chat={'id':9,'type':'private'}
        sender={'id':9}
        callback={'id':'query','data':'subscription_widget','message':{'chat':chat},'from':sender}
        self.assertTrue(show_subscription('fixture',{'callback_query':callback},ready=True))
        telegram.assert_called_once_with('fixture','answerCallbackQuery',{'callback_query_id':'query'},timeout=3)
        api.assert_called_once_with('subscription-widget/open',{'telegram_user_id':9})
        api.reset_mock()
        mismatched = {**callback, 'from':{'id':15}}
        self.assertFalse(show_subscription('fixture',{'callback_query':mismatched},ready=True))
        api.assert_not_called()
        self.assertTrue(show_subscription('fixture',{'message':{'chat':chat,'from':sender,'text':'/subscriptions'}},ready=True))
        api.assert_called_once_with('subscription-widget/open',{'telegram_user_id':9})
