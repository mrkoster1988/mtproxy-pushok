"""Маршрутизация Bot API и граница test/production без внешних запросов."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi import HTTPException, Response
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import telegram_environment as api_env, stars_checkout, channel_verification
from app.bot_stars import authorize, eligible, StarsPrepareOutput
from bot import telegram_environment as bot_env, main as bot
from test_bot_stars_api import row


class TelegramEnvironmentTests(unittest.TestCase):
    def test_fixed_hosts_modes_and_invalid_configuration(self):
        for module in (api_env, bot_env):
            for flag, middle in [('0', '/'), ('1', '/test/')]:
                with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': flag}):
                    self.assertEqual(module.bot_api_url('12345:fixture', 'getMe'),
                                     'https://api.telegram.org/bot12345:fixture'+middle+'getMe')
                    for token, method in [('secret/evil','getMe'),('secret','../getMe')]:
                        with self.assertRaises(ValueError): module.bot_api_url(token,method)
            for flag in ('true','', '2', ' 1'):
                with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':flag}):
                    with self.assertRaises((ValueError,HTTPException)):
                        module.bot_api_url('fixture','getMe')

    def test_bot_transport_and_internal_marker(self):
        stream=MagicMock();stream.__enter__.return_value=stream
        stream.read.return_value=b'{"ok":true,"result":{}}'
        with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1','BOT_INTERNAL_SECRET':'b'*64}), patch.object(bot,'urlopen',return_value=stream) as transport:
            bot.telegram_call('fixture','getMe',{})
            self.assertEqual(transport.call_args.args[0].full_url,'https://api.telegram.org/botfixture/test/getMe')
            bot._internal_api('stars/prepare',{})
            self.assertEqual(transport.call_args.args[0].get_header('X-pushok-telegram-environment'),'test')

    def test_invoice_and_channel_transports_use_test_endpoint(self):
        stream=MagicMock();stream.__enter__.return_value=stream;stream.status=200
        stream.read.return_value=b'{"ok":true,"result":"https://t.me/$fixture"}'
        opener=MagicMock();opener.open.return_value=stream
        with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):
            with patch.object(stars_checkout,'build_opener',return_value=opener):
                stars_checkout.create_invoice_link('fixture',StarsPrepareOutput(invoice_payload='stars:fixture',stars_amount=1,title='Test',description='Test'))
                self.assertEqual(opener.open.call_args.args[0].full_url,'https://api.telegram.org/botfixture/test/createInvoiceLink')
            stream.read.return_value=b'{"ok":true,"result":{}}'
            with patch.object(channel_verification,'urlopen',return_value=stream) as transport:
                channel_verification._telegram_call('fixture','getChat',{})
                self.assertEqual(transport.call_args.args[0].full_url,'https://api.telegram.org/botfixture/test/getChat')

    def test_authorization_requires_matching_bot_environment(self):
        with patch.dict(os.environ, {'BOT_INTERNAL_SECRET':'b'*64}):
            for flag, accepted in [('0',(None,'production')),('1',('test',))]:
                with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':flag}):
                    for marker in (None,'test','production','other'):
                        if marker in accepted: authorize('b'*64,Response(),marker)
                        else:
                            with self.assertRaises(HTTPException) as error: authorize('b'*64,Response(),marker)
                            self.assertEqual(error.exception.status_code,403)

    def test_attempt_environment_is_part_of_payment_eligibility(self):
        for flag in ('0','1'):
            with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':flag}):
                self.assertTrue(eligible({**row(),'telegram_test_environment':flag=='1'},42,'XTR',180))
                self.assertFalse(eligible({**row(),'telegram_test_environment':flag!='1'},42,'XTR',180))
