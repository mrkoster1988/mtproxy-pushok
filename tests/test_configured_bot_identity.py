import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from api.app.telegram_environment import configured_bot_username
from api.app.channel_subscription import ChannelSubscriptionSettings


class ConfiguredBotIdentityTests(unittest.TestCase):
    def test_username_is_read_from_configuration(self):
        with patch.dict(os.environ, TELEGRAM_BOT_USERNAME='@NewPushokBot'):
            self.assertEqual(configured_bot_username(), 'NewPushokBot')
        with patch.dict(os.environ, TELEGRAM_BOT_USERNAME=''):
            self.assertEqual(configured_bot_username(), '')
        with patch.dict(os.environ, TELEGRAM_BOT_USERNAME='https://t.me/NewPushokBot'):
            self.assertEqual(configured_bot_username(), '')

    def test_channel_username_is_normalized(self):
        with patch.dict(os.environ, TELEGRAM_BOT_USERNAME='NewPushokBot'):
            self.assertEqual(ChannelSubscriptionSettings(enabled=True, channel_url='@ExampleChannel').channel_url, 'https://t.me/ExampleChannel')
            self.assertEqual(ChannelSubscriptionSettings(enabled=True, channel_url='t.me/ExampleChannel/').channel_url, 'https://t.me/ExampleChannel')
            with self.assertRaises(ValidationError):
                ChannelSubscriptionSettings(enabled=True, channel_url='@newpushokbot')
