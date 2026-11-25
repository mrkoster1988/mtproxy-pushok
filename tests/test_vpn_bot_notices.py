from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from bot.vpn_notices import deliver


class VpnNotices(TestCase):
    def test_expiry_opens_paid_checkout_and_commits_receipt(self):
        row={'id':7,'lease':'ab'*16,'telegram_id':123,'text':'Pushok ИИ','renew':True}
        api=Mock(side_effect=[{'items':[row]},{}])
        send=Mock()
        with patch('bot.vpn_notices.is_test_environment',return_value=False):
            deliver(api,send)
        payload=send.call_args.args[0]
        self.assertEqual(payload['chat_id'],123)
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['url'],'https://pushok.shop/shop/?renew=wg#plans')
        self.assertEqual(api.call_args.args,('vpn-notices/finish',{'id':7,'lease':row['lease'],'status':'sent'}))

    def test_delivery_errors_and_test_environment(self):
        event={'id':1,'lease':'ab'*16,'telegram_id':123,'text':'Pushok ИИ','renew':False}
        for code,status in [(403,'blocked'),(429,'retry')]:
            api=Mock(side_effect=[{'items':[event]},{}])
            with patch('bot.vpn_notices.is_test_environment',return_value=False):
                deliver(api,Mock(side_effect=HTTPError('https://example.invalid',code,'test',{},None)))
            self.assertEqual(api.call_args.args[1]['status'],status)
        api=Mock()
        with patch('bot.vpn_notices.is_test_environment',return_value=True):
            deliver(api,Mock())
        api.assert_not_called()
