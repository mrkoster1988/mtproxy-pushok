"""The Flow handoff opens existing authentication and never decides access."""
import os
import unittest
from unittest.mock import Mock, patch

from bot.main import response_for


class FlowBotTests(unittest.TestCase):
    code = "2345ABCDEFGH"
    settings = {
        "PUSHOK_MINIAPP_ENABLED": "1",
        "PUSHOK_MINIAPP_URL": "https://pushok.example/miniapp",
        "TELEGRAM_BOT_USERNAME": "mtproxy_pushok_bot",
    }

    def update(self, text=None, *, actor=42, chat=42, chat_type="private", is_bot=False):
        return {"message": {
            "chat": {"id": chat, "type": chat_type},
            "from": {"id": actor, "is_bot": is_bot},
            "text": text if text is not None else "/start flow_" + self.code,
        }}

    def test_private_handoff_contains_only_public_code_and_requires_confirmation(self):
        # These hooks must not execute: a deep link only creates a local reply.
        forbidden = Mock(side_effect=AssertionError("Flow handoff must not access accounts"))
        with patch.dict(os.environ, self.settings), patch("bot.main.telegram_call", forbidden):
            payload, callback = response_for(self.update(), campaign_loader=forbidden,
                wizard=forbidden, submitter=forbidden, referral_handler=forbidden)
        self.assertIsNone(callback)
        self.assertEqual(payload["chat_id"], 42)
        self.assertIn("2345 ABCD EFGH", payload["text"])
        self.assertIn("только после вашего подтверждения", payload["text"])
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": [[{
            "text": "Подтвердить в кабинете",
            "web_app": {"url": "https://pushok.example/miniapp?flow=" + self.code},
        }]]})
        forbidden.assert_not_called()

    def test_configured_bot_mention_and_trailing_slash_are_supported(self):
        with patch.dict(os.environ, {**self.settings, "TELEGRAM_BOT_USERNAME": "OtherPushokBot",
                                    "PUSHOK_MINIAPP_URL": "https://pushok.example/miniapp/"}):
            result = response_for(self.update("/START@OtherPushokBot flow_" + self.code))
            self.assertEqual(result[0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                             "https://pushok.example/miniapp/?flow=" + self.code)
            self.assertIsNone(response_for(self.update("/start@mtproxy_pushok_bot flow_" + self.code)))

    def test_foreign_missing_bot_and_group_senders_cannot_get_flow_handoff(self):
        cases = [dict(actor=43), dict(actor=None), dict(actor=True), dict(actor="42"),
                 dict(actor=-42, chat=-42), dict(actor=1, chat=True),
                 dict(is_bot=True), dict(chat_type="group"), dict(chat_type="supergroup")]
        with patch.dict(os.environ, self.settings):
            for case in cases:
                with self.subTest(case=case):
                    self.assertIsNone(response_for(self.update(**case)))

    def test_malformed_payload_never_reaches_flow_confirmation(self):
        payloads = [self.code.lower(), self.code[:-1], self.code + "A", "123456789ABC",
                    "2345ABCDEFGI", "2345ABCDEFG0", self.code + "?next=https://other.example",
                    self.code + " extra", self.code + "\nextra", "../../miniapp"]
        with patch.dict(os.environ, self.settings):
            for payload in payloads:
                with self.subTest(payload=payload):
                    reply = response_for(self.update("/start flow_" + payload))
                    self.assertNotIn("?flow=", str(reply))
                    self.assertNotIn("Подтвердить в кабинете", str(reply))

    def test_disabled_or_untrusted_miniapp_has_no_handoff_url(self):
        cases = [{"PUSHOK_MINIAPP_ENABLED": "0"},
                 {"PUSHOK_MINIAPP_URL": "http://pushok.example/miniapp"},
                 {"PUSHOK_MINIAPP_URL": "https://pushok.example/miniapp?redirect=other"},
                 {"PUSHOK_MINIAPP_URL": "https://user:secret@pushok.example/miniapp"},
                 {"PUSHOK_MINIAPP_URL": "https://pushok.example/other"}]
        for case in cases:
            with self.subTest(case=case), patch.dict(os.environ, {**self.settings, **case}):
                reply = response_for(self.update())
                self.assertIn("пока недоступен", reply[0]["text"])
                self.assertNotIn("reply_markup", reply[0])


if __name__ == "__main__":
    unittest.main()
