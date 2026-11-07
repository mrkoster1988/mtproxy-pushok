"""Проверки команд и безопасных ответов бота."""
import unittest
from pathlib import Path
from unittest.mock import patch, call
from bot.main import CHANNEL_URL, COMMANDS, keyboard, response_for, validate_bot


class TelegramBotTests(unittest.TestCase):
    def test_all_registered_commands_have_responses(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                response = response_for({"message": {
                    "chat": {"id": 123, "type": "private"},
                    "text": "/" + command}})
                self.assertIsNotNone(response)
                self.assertEqual(response[0]["chat_id"], 123)
                self.assertIn("inline_keyboard", response[0]["reply_markup"])

    def test_bot_container_is_optional_and_isolated(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "compose.yaml").read_text(encoding="utf-8")
        bot_service = config.split("\n  bot:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        self.assertIn('profiles: ["bot"]', bot_service)
        self.assertIn("read_only: true", bot_service)
        self.assertIn("no-new-privileges:true", bot_service)
        self.assertIn("- bot-egress", bot_service)
        self.assertIn("- bot-api", bot_service)
        self.assertIn("BOT_INTERNAL_SECRET:", bot_service)
        self.assertIn("  bot-api:\n    internal: true".replace("\\n", "\\n"), config)
        self.assertNotIn("env_file:", bot_service)
        self.assertNotIn("- internal", bot_service)

    def test_validates_identity_and_polling_mode(self):
        with patch("bot.main.telegram_call", side_effect=[
            {"username": "mtproxy_pushok_bot"}, {"url": ""}
        ]) as client:
            validate_bot("test-token")
        self.assertEqual(client.call_args_list, [
            call("test-token", "getMe", {}),
            call("test-token", "getWebhookInfo", {}),
        ])

    def test_other_bot_token_rejected(self):
        with patch("bot.main.telegram_call", return_value={"username": "wrong_bot"}):
            with self.assertRaisesRegex(RuntimeError, "другому Telegram-боту"):
                validate_bot("test-token")

    def test_active_webhook_rejected(self):
        with patch("bot.main.telegram_call", side_effect=[
            {"username": "mtproxy_pushok_bot"}, {"url": "https://example.org/webhook"}
        ]):
            with self.assertRaisesRegex(RuntimeError, "Webhook"):
                validate_bot("test-token")

    def test_others_and_groups_ignored(self):
        self.assertIsNone(response_for({"message": {
            "chat": {"id": 1, "type": "group"}, "text": "/start"}}))
        self.assertIsNone(response_for({"message": {
            "chat": {"id": 1, "type": "private"}, "text": "/start@other_bot"}}))
        self.assertIsNone(response_for({"message": {
            "chat": {"id": 1, "type": "private"}, "text": "hello"}}))
        self.assertIsNone(response_for({"message": {
            "chat": {"id": 1, "type": "private"}, "text": "/unknown"}}))

    def test_callback_and_links(self):
        response = response_for({"callback_query": {
            "id": "callback1", "data": "tariffs",
            "message": {"chat": {"id": 17, "type": "private"}}}})
        self.assertEqual(response[1], "callback1")
        self.assertIn("300 ₽", response[0]["text"])
        self.assertEqual(keyboard()["inline_keyboard"][2][0]["url"], CHANNEL_URL)


if __name__ == "__main__":
    unittest.main()
