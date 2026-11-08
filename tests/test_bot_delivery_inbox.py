"""Telegram интерфейс диагностики: только приватный admin, безопасный retry."""
import unittest
from urllib.error import HTTPError
from uuid import UUID

from bot.delivery_inbox import DeliveryInbox
from bot.main import response_for

NOTICE = UUID("00000000-0000-4000-8000-000000000042")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000043")


def message(uid=81, content="/delivery", chat=None):
    return {"message": {
        "chat": {"id": uid if chat is None else chat, "type": "private"},
        "from": {"id": uid}, "text": content,
    }}


def callback(data, uid=81, chat=None):
    return {"callback_query": {
        "id": "press1", "data": data,
        "from": {"id": uid},
        "message": {"chat": {"id": uid if chat is None else chat, "type": "private"}},
    }}


class DeliveryBotTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def report(uid):
            self.calls.append(("report", uid))
            return {
                "advertiser": {"waiting": 2, "in_flight": 1, "delivered": 4, "abandoned": 3},
                "operator": {"waiting": 1, "in_flight": 0, "delivered": 5},
                "recent_undelivered": [
                    {"notice_id": str(NOTICE), "campaign_id": str(CAMPAIGN),
                     "status": "waiting", "attempts": 3},
                ],
            }
        def retry(uid, notice):
            self.calls.append(("retry", uid, notice))
            return {"queued": True}
        self.inbox = DeliveryInbox(report, retry)

    def respond(self, update):
        return response_for(update, delivery_inbox=self.inbox)

    def test_admin_report_contains_aggregate_counts_and_retry_button(self):
        result = self.respond(message())
        self.assertEqual(self.calls, [("report", 81)])
        self.assertIn("ожидают 2", result[0]["text"])
        self.assertIn("не доставить 3", result[0]["text"])
        buttons = result[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["callback_data"],
                         "delivery:retry:" + str(NOTICE))

    def test_retry_callback_calls_api_and_does_not_claim_delivery(self):
        result = self.respond(callback("delivery:retry:" + str(NOTICE)))
        self.assertEqual(self.calls, [("retry", 81, NOTICE)])
        self.assertIn("не подтверждение доставки", result[0]["text"])

    def test_refresh_action_reloads_report(self):
        self.respond(callback("delivery:refresh"))
        self.assertEqual(self.calls, [("report", 81)])

    def test_foreign_chat_id_never_calls_api(self):
        result = self.respond(message(chat=82))
        self.assertIsNone(result)
        result = self.respond(callback("delivery:retry:" + str(NOTICE), chat=82))
        self.assertIsNone(result)
        self.assertFalse(self.calls)

    def test_regular_non_delivery_message_ignored(self):
        self.assertIsNone(self.respond(message(content="/start")))
        self.assertFalse(self.calls)

    def test_operator_role_denied_by_server(self):
        def forbidden(*args):
            raise HTTPError("https://internal.example", 403, "No", {}, None)
        self.inbox = DeliveryInbox(forbidden, forbidden)
        report = self.respond(message())
        self.assertIn("Только администратор", report[0]["text"])
        retry = self.respond(callback("delivery:retry:" + str(NOTICE)))
        self.assertIn("только администратору", retry[0]["text"])

    def test_retry_disabled_for_blocked_or_active(self):
        self.inbox.report_loader = lambda _: {
            "advertiser": {"waiting": 0, "in_flight": 1, "delivered": 1, "abandoned": 1},
            "operator": {"waiting": 0, "in_flight": 0, "delivered": 0},
            "recent_undelivered": [
                {"notice_id": str(NOTICE), "campaign_id": str(CAMPAIGN),
                 "status": "abandoned", "attempts": 3},
            ],
        }
        report = self.respond(message())
        self.assertNotIn("delivery:retry:", str(report[0]["reply_markup"]))
        self.assertIn("постоянный отказ", report[0]["text"])

    def test_invalid_uuid_rejected_without_backend(self):
        response = self.respond(callback("delivery:retry:garbage"))
        self.assertIn("Некорректный идентификатор", response[0]["text"])
        self.assertFalse(self.calls)

    def test_retry_of_delivered_notice_is_explained(self):
        def conflict(*args):
            raise HTTPError("https://internal.example", 409, "Already", {}, None)
        self.inbox.retry = conflict
        response = self.respond(callback("delivery:retry:" + str(NOTICE)))
        self.assertIn("Повтор сейчас недоступен", response[0]["text"])

    def test_report_requires_payload(self):
        self.inbox.report_loader = lambda _: {"advertiser": {}}
        result = self.respond(message())
        self.assertIn("временно недоступен", result[0]["text"])


if __name__ == "__main__":
    unittest.main()
