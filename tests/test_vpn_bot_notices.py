from io import BytesIO
import json
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
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['web_app']['url'],'https://pushok.shop/miniapp?vpn=renew')
        self.assertEqual(api.call_args.args,('vpn-notices/finish',{'id':7,'lease':row['lease'],'status':'sent'}))

    def test_delivery_errors_and_test_environment(self):
        event={'id':1,'lease':'ab'*16,'telegram_id':123,'text':'Pushok ИИ','renew':False}
        for code,detail,status in [(403,'recipient_blocked','blocked'),(403,'invalid_bridge','retry'),
                                   (429,'rate_limit','retry'),(503,'cabinet_bot_chat_not_started','waiting_chat')]:
            api=Mock(side_effect=[{'items':[event]},{}])
            with patch('bot.vpn_notices.is_test_environment',return_value=False):
                deliver(api,Mock(side_effect=HTTPError('https://example.invalid',code,'test',{},BytesIO(json.dumps({'detail':detail}).encode()))))
            self.assertEqual(api.call_args.args[1]['status'],status)
        api=Mock()
        with patch('bot.vpn_notices.is_test_environment',return_value=True):
            deliver(api,Mock())
        api.assert_not_called()

    def test_reminder_claim_can_wait_for_shop_without_extending_checkout_timeout(self):
        import io
        from bot import main
        with patch.dict('os.environ', BOT_INTERNAL_SECRET='ab'*32), patch.object(main, 'urlopen') as open_url:
            for route,timeout in [('vpn-reminders/claim',25),('stars/pre-checkout',5)]:
                open_url.return_value.__enter__.return_value=io.BytesIO(b'{}')
                main._internal_api(route,{})
                self.assertEqual(open_url.call_args.kwargs['timeout'],timeout)
