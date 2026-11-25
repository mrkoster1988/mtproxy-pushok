import os
import unittest
from unittest.mock import patch
from bot.main import response_for


class SubscriptionCommandTests(unittest.TestCase):
    @patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1', 'PUSHOK_MINIAPP_URL': 'https://pushok.example/miniapp'})
    def test_private_chat_opens_customer_and_separate_staff_entry(self):
        msg = {'chat': {'id': 9, 'type': 'private'}, 'from': {'id': 9}, 'text': '/subscriptions'}
        payload, _ = response_for({'message': msg})
        urls = [row[0]['web_app']['url'] for row in payload['reply_markup']['inline_keyboard']]
        self.assertEqual(urls, ['https://pushok.example/miniapp?shop_subscriptions=1',
                                'https://pushok.example/shop/admin?section=users'])
        msg['chat']['type'] = 'group'
        self.assertIsNone(response_for({'message': msg}))
