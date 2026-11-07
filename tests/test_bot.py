"""Проверки команд и безопасных ответов бота."""
import unittest
from bot.main import CHANNEL_URL, COMMANDS, keyboard, response_for


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
