"""Юнит-тесты прав администратора рекламируемого Telegram-канала."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, call

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.channel_verification import (
    verify_channel_admin, ChannelNotVerified, TelegramUnavailable,
)

BOT = {"id": 12345, "username": "mtproxy_pushok_bot", "is_bot": True}
CHANNEL = {"id": -1001234567890, "username": "ExampleChannel", "type": "channel"}


class ChannelVerificationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "12345:fake_test_only"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_verified_channel_and_user_administrator(self):
        with patch("app.channel_verification._telegram_call", side_effect=[
            BOT, CHANNEL, {"status": "administrator"}, {"status": "creator"},
        ]) as api:
            result = verify_channel_admin("ExampleChannel", 88)
        self.assertEqual(result, CHANNEL["id"])
        self.assertEqual(api.call_args_list, [
            call("12345:fake_test_only", "getMe", {}),
            call("12345:fake_test_only", "getChat", {"chat_id": "@ExampleChannel"}),
            call("12345:fake_test_only", "getChatMember", {
                "chat_id": CHANNEL["id"], "user_id": BOT["id"]
            }),
            call("12345:fake_test_only", "getChatMember", {
                "chat_id": CHANNEL["id"], "user_id": 88
            }),
        ])

    def test_bot_must_be_admin(self):
        with patch("app.channel_verification._telegram_call", side_effect=[
            BOT, CHANNEL, {"status": "member"},
        ]) as api:
            with self.assertRaises(ChannelNotVerified):
                verify_channel_admin("ExampleChannel", 88)
        self.assertEqual(api.call_count, 3)

    def test_user_must_be_admin(self):
        with patch("app.channel_verification._telegram_call", side_effect=[
            BOT, CHANNEL, {"status": "administrator"}, {"status": "member"},
        ]):
            with self.assertRaises(ChannelNotVerified):
                verify_channel_admin("ExampleChannel", 88)

    def test_wrong_channel_username_or_chat_type(self):
        for chat in (
            {**CHANNEL, "username": "OtherChannel"},
            {**CHANNEL, "type": "supergroup"},
            {**CHANNEL, "id": 222},
        ):
            with self.subTest(chat=chat):
                with patch("app.channel_verification._telegram_call", side_effect=[
                    BOT, chat,
                ]) as api:
                    with self.assertRaises(ChannelNotVerified):
                        verify_channel_admin("ExampleChannel", 88)
                self.assertEqual(api.call_count, 2)

    def test_wrong_bot_token_is_not_accepted(self):
        with patch("app.channel_verification._telegram_call", return_value={
            **BOT, "username": "another_bot",
        }) as api:
            with self.assertRaises(TelegramUnavailable):
                verify_channel_admin("ExampleChannel", 88)
        self.assertEqual(api.call_count, 1)

    def test_missing_token_is_not_accepted(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}):
            with patch("app.channel_verification._telegram_call") as api:
                with self.assertRaises(TelegramUnavailable):
                    verify_channel_admin("ExampleChannel", 88)
                api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
