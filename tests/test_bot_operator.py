"""Бот: роль оператора проверяет API, кнопки не обходят модерацию."""
import unittest
from urllib.error import HTTPError
from uuid import UUID

from bot.main import response_for, send_operator_notifications
from bot.operator import OperatorMenu, notification_payload

CID = "00000000-0000-4000-8000-000000000197"
ITEM = {
    "id": CID, "channel_title": "Тестовый канал",
    "channel_url": "https://t.me/MyChannel",
    "price_kopecks_snapshot": 30000,
    "currency_snapshot": "RUB", "created_at": "2026-11-08T08:00:00+00:00",
}


def message(uid, text, chat=None, chat_type="private"):
    return {"message": {"chat": {
        "id": uid if chat is None else chat, "type": chat_type
    }, "from": {"id": uid}, "text": text}}


def callback(uid, action, chat=None, chat_type="private"):
    return {"callback_query": {
        "id": "op-callback", "data": action,
        "from": {"id": uid},
        "message": {"chat": {
            "id": uid if chat is None else chat, "type": chat_type,
        }},
    }}


def error(code):
    # Токены и секреты не должны попасть в ответы пользователям.
    return HTTPError("https://api.telegram.org/botSECRET", code, "error", {}, None)


class OperatorMenuTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100
        self.decisions = []
        def review(uid, campaign, decision, comment):
            self.decisions.append((uid, campaign, decision, comment))
            return {"moderation_status": decision}
        self.menu = OperatorMenu(
            lambda uid: [ITEM], review,
            lambda uid: [{
                "campaign_id": CID, "channel_title": "Тестовый канал",
                "decision": "approved",
            }],
            clock=lambda: self.clock,
        )

    def respond(self, update):
        return response_for(update, operator_menu=self.menu)

    def test_operator_sees_pending_with_buttons(self):
        result = self.respond(message(5, "/moderation"))
        self.assertIn("Очередь", result[0]["text"])
        self.assertIn("Тестовый канал", result[0]["text"])
        self.assertIn("300 ₽", result[0]["text"])
        buttons = result[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["callback_data"], "op:a:" + CID)
        self.assertEqual(buttons[1][0]["callback_data"], "op:c:" + CID)
        self.assertEqual(buttons[1][1]["callback_data"], "op:r:" + CID)

    def test_approve_uses_sender_id(self):
        result = self.respond(callback(5, "op:a:" + CID))
        self.assertIn("Одобрено", result[0]["text"])
        self.assertEqual(self.decisions, [(5, UUID(CID), "approved", None)])
        self.assertEqual(result[1], "op-callback")

    def test_request_changes_requires_reason(self):
        prompt = self.respond(callback(5, "op:c:" + CID))
        self.assertIn("Напишите причину", prompt[0]["text"])
        reject_blank = self.respond(message(5, "  "))
        self.assertIn("1 до 2000", reject_blank[0]["text"])
        self.assertEqual(self.decisions, [])
        final = self.respond(message(5, "Измените ссылку на канал."))
        self.assertIn("Возвращено", final[0]["text"])
        self.assertEqual(self.decisions, [(5, UUID(CID), "changes_requested",
                                           "Измените ссылку на канал.")])
        self.assertFalse(self.menu.sessions)

    def test_reject_requires_comment_and_can_cancel(self):
        self.respond(callback(5, "op:r:" + CID))
        self.respond(message(5, "/cancel"))
        self.assertEqual(self.decisions, [])
        self.assertEqual(self.menu.sessions, {})
        self.assertIsNone(self.respond(message(5, "Причина после отмены")))

    def test_reject_saves_comment(self):
        self.respond(callback(5, "op:r:" + CID))
        result = self.respond(message(5, "Нарушение условий"))
        self.assertIn("Отклонено", result[0]["text"])
        self.assertEqual(self.decisions[-1], (5, UUID(CID), "rejected",
                                              "Нарушение условий"))

    def test_history_only_requested_for_sender(self):
        result = self.respond(callback(5, "op:h"))
        self.assertIn("Тестовый канал", result[0]["text"])
        self.assertIn("Одобрено", result[0]["text"])

    def test_api_rejects_non_operator_with_safe_message(self):
        menu = OperatorMenu(lambda uid: (_ for _ in ()).throw(error(403)),
                            self.menu.reviewer, self.menu.history_loader)
        result = response_for(message(5, "/moderation"), operator_menu=menu)
        self.assertIn("Нет доступа", result[0]["text"])
        self.assertNotIn("SECRET", result[0]["text"])

    def test_api_rejects_stale_campaign(self):
        menu = OperatorMenu(self.menu.queue_loader,
                            lambda *_: (_ for _ in ()).throw(error(409)),
                            self.menu.history_loader)
        result = response_for(callback(5, "op:a:" + CID), operator_menu=menu)
        self.assertIn("уже обработана", result[0]["text"])
        self.assertNotIn("SECRET", result[0]["text"])

    def test_forged_callback_and_group_not_processed(self):
        self.assertIsNone(self.respond(callback(5, "op:a:" + CID, chat=6)))
        self.assertIsNone(self.respond(message(5, "/moderation", chat_type="group")))
        self.assertEqual(self.decisions, [])

    def test_comment_session_expires(self):
        self.respond(callback(5, "op:c:" + CID))
        self.clock += 601
        self.assertIsNone(self.respond(message(5, "Запоздавший комментарий")))
        self.assertEqual(self.decisions, [])

    def test_notification_sent_then_acknowledged(self):
        notices = []
        def inner(route, payload):
            if route == "operator/notifications":
                return [ITEM]
            notices.append(("ack", route, payload))
            return {"acknowledged": True}
        def send(token, method, payload):
            notices.append(("send", method, payload))
            return {}
        from unittest.mock import patch
        with patch("bot.main._internal_api", side_effect=inner), \
             patch("bot.main.telegram_call", side_effect=send):
            send_operator_notifications("test-token", 5)
        self.assertEqual(notices[0][0:2], ("send", "sendMessage"))
        self.assertEqual(notices[1][0:2], ("ack", "operator/notifications/ack"))
        self.assertEqual(notices[1][2]["campaign_id"], CID)

    def test_failed_send_does_not_acknowledge(self):
        from unittest.mock import patch
        calls = []
        def api(route, payload):
            calls.append(route)
            return [ITEM]
        with patch("bot.main._internal_api", side_effect=api), \
             patch("bot.main.telegram_call", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                send_operator_notifications("test-token", 5)
        self.assertEqual(calls, ["operator/notifications"])

    def test_notification_message_has_moderation_buttons(self):
        item = notification_payload(ITEM, 5)
        self.assertEqual(item["chat_id"], 5)
        self.assertEqual(item["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                         "op:a:" + CID)


if __name__ == "__main__":
    unittest.main()
