"""Регрессии безопасных опциональных значков Telegram на кнопках Pushok."""
import os
import unittest
from unittest.mock import patch
from bot.button_icons import configured_icons, decorate


class TestButtonIcons(unittest.TestCase):
    def test_disabled_keeps_buttons_unchanged(self):
        with patch.dict(os.environ, {"PUSHOK_BUTTON_ICONS_ENABLED": "0", "PUSHOK_BUTTON_EMOJI_JSON": '{"advertise":"123456789012"}'}):
            self.assertEqual(configured_icons(), {})
            original = {"text": "Кнопка", "callback_data": "start"}
            self.assertEqual(decorate(original, "advertise"), original)

    def test_registered_numeric_id_is_added(self):
        with patch.dict(os.environ, {"PUSHOK_BUTTON_ICONS_ENABLED": "1", "PUSHOK_BUTTON_EMOJI_JSON": '{"advertise":"1234567890123456789"}'}):
            original = {"text": "Кнопка", "callback_data": "start"}
            actual = decorate(original, "advertise")
            self.assertEqual(actual["icon_custom_emoji_id"], "1234567890123456789")
            self.assertEqual(actual["callback_data"], "start")
            self.assertNotIn("icon_custom_emoji_id", original)

    def test_bad_config_never_breaks_menu(self):
        for raw in ("oops", '[]', '{"advertise":1}', '{"advertise":"not-an-id"}', '{"unknown":"123456789"}'):
            with self.subTest(raw=raw), patch.dict(os.environ, {"PUSHOK_BUTTON_ICONS_ENABLED": "1", "PUSHOK_BUTTON_EMOJI_JSON": raw}):
                self.assertEqual(configured_icons(), {})
                self.assertEqual(decorate({"text": "Ок"}, "advertise"), {"text": "Ок"})

    def test_real_bot_keyboard_retains_callback_and_links(self):
        from bot.main import keyboard, CHANNEL_URL
        with patch.dict(os.environ, {
            "PUSHOK_BUTTON_ICONS_ENABLED": "1",
            "PUSHOK_BUTTON_EMOJI_JSON": '{"advertise":"123456789012","channels":"123456789013"}',
            "PUSHOK_MINIAPP_ENABLED": "0",
        }):
            rows = keyboard()["inline_keyboard"]
        self.assertEqual(rows[0][0]["callback_data"], "advertise")
        self.assertEqual(rows[0][0]["icon_custom_emoji_id"], "123456789012")
        channel = next(b for row in rows for b in row if b.get("url") == CHANNEL_URL)
        self.assertEqual(channel["icon_custom_emoji_id"], "123456789013")
        self.assertNotIn("icon_custom_emoji_id", rows[1][0])


if __name__ == "__main__":
    unittest.main()
