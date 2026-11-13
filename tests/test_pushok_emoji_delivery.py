"""Однократное восстановление меню после явного отказа по иконке."""
import copy
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from bot import main as bot


def error(code=400, description="Bad Request: icon_custom_emoji_id is invalid"):
    return HTTPError("https://api.telegram.org/redacted", code, "redacted", {},
                     io.BytesIO(json.dumps({"ok": False, "error_code": code, "description": description}).encode()))


def success():
    return io.BytesIO(b'{"ok": true, "result": {"message_id": 42}}')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"chat_id": 123, "text": "menu", "reply_markup": {"inline_keyboard": [[
            {"text": "Ad", "callback_data": "advertise", "icon_custom_emoji_id": "123456789"},
            {"text": "Channel", "url": "https://t.me/pushok_proxy", "icon_custom_emoji_id": "123456788"},
            {"text": "App", "web_app": {"url": "https://example.org/miniapp"}, "icon_custom_emoji_id": "123456787"},
            {"text": "Stars", "callback_data": "stars:fixture"}]]}}

    def test_explicit_http_rejection_retries_once_and_preserves_every_action(self):
        original = copy.deepcopy(self.payload)
        with patch.object(bot, "urlopen", side_effect=[error(), success()]) as send:
            self.assertEqual(bot.telegram_call("fixture", "sendMessage", self.payload), {"message_id": 42})
        self.assertEqual(send.call_count, 2)
        colored = copy.deepcopy(original)
        for button, style in zip(colored["reply_markup"]["inline_keyboard"][0],
                                 ("success", "primary", "primary", "success")):
            button["style"] = style
        self.assertEqual(json.loads(send.call_args_list[0].args[0].data), colored)
        retried = json.loads(send.call_args_list[1].args[0].data)
        expected = copy.deepcopy(colored)
        for button in expected["reply_markup"]["inline_keyboard"][0]:
            button.pop("icon_custom_emoji_id", None)
        self.assertEqual(retried, expected)
        self.assertEqual(self.payload, original)

    def test_json_rejection_and_second_rejection(self):
        rejected = io.BytesIO(b'{"ok":false,"error_code":400,"description":"BUTTON_ICON_INVALID"}')
        with patch.object(bot, "urlopen", side_effect=[rejected, error()]) as send:
            with self.assertRaises(HTTPError):
                bot.telegram_call("fixture", "sendMessage", self.payload)
            self.assertEqual(send.call_count, 2)

    def test_no_retries_on_ambiguous_or_unrelated_errors(self):
        for failure in (TimeoutError(), URLError("offline"), error(429), error(500),
                        error(403), error(description="Bad Request: chat not found"),
                        HTTPError("redacted", 400, "invalid", {}, io.BytesIO(b"not JSON")),
                        HTTPError("redacted", 400, "large", {}, io.BytesIO(b"x" * 65537))):
            with self.subTest(failure=type(failure)), patch.object(bot, "urlopen", side_effect=failure) as send:
                with self.assertRaises((OSError, HTTPError, URLError)):
                    bot.telegram_call("fixture", "sendMessage", self.payload)
                self.assertEqual(send.call_count, 1)

    def test_success_disabled_flag_and_payment_methods_never_retry(self):
        with patch.object(bot, "urlopen", return_value=success()) as send:
            bot.telegram_call("fixture", "sendMessage", self.payload)
            self.assertEqual(send.call_count, 1)
        for method in ("sendInvoice", "answerPreCheckoutQuery", "refundStarPayment"):
            with patch.object(bot, "urlopen", side_effect=error()) as send, self.assertRaises(HTTPError):
                try:
                    bot.telegram_call("fixture", method, self.payload)
                finally:
                    self.assertEqual(send.call_count, 1)
                    self.assertEqual(json.loads(send.call_args.args[0].data), self.payload)
        with patch.dict(os.environ, {"PUSHOK_BUTTON_ICONS_ENABLED": "0"}):
            plain = {"chat_id": 123, "text": "menu", "reply_markup": bot.keyboard()}
        with patch.object(bot, "urlopen", side_effect=error()) as send, self.assertRaises(HTTPError):
            try:
                bot.telegram_call("fixture", "sendMessage", plain)
            finally:
                self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
