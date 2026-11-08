"""Проверки Telegram-очереди: права, решения и комментарии."""
import unittest
from urllib.error import HTTPError
from uuid import UUID
from unittest.mock import patch

from bot.operator_inbox import OperatorInbox
from bot.main import response_for, submit_campaign_draft

CID = UUID("00000000-0000-4000-8000-000000000123")


def msg(text, uid=77, chat=None):
    return {"message": {
        "chat": {"id": uid if chat is None else chat, "type": "private"},
        "from": {"id": uid}, "text": text,
    }}


def cb(action, uid=77, chat=None):
    return {"callback_query": {
        "id": "test-callback", "data": action,
        "from": {"id": uid},
        "message": {"chat": {"id": uid if chat is None else chat, "type": "private"}},
    }}


class BotOperatorInboxTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.queue = {
            "subscribed": True,
            "pending": [{
                "campaign_id": str(CID),
                "channel_title": "@testchannel",
                "channel_url": "https://t.me/testchannel",
                "tariff_name": "1 день", "price_kopecks": 30000, "currency": "RUB",
            }],
        }
        def queue_loader(user, subscribe=False):
            self.calls.append(("queue", user, subscribe))
            return self.queue
        def decider(user, campaign, decision, comment):
            self.calls.append(("decide", user, campaign, decision, comment))
            return {"moderation_status": decision}
        self.inbox = OperatorInbox(queue_loader, decider,
                                   lambda user: self.calls.append(("off", user)),
                                   clock=lambda: 100.0)

    def run_update(self, update):
        return response_for(update, operator_inbox=self.inbox)

    def test_subscribe_and_queue_shows_buttons(self):
        result = self.run_update(msg("/moderation"))
        self.assertEqual(self.calls, [("queue", 77, True)])
        self.assertIn("300 ₽", result[0]["text"])
        buttons = result[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(buttons[0][0]["callback_data"], f"review:approve:{CID}")
        self.assertEqual(buttons[0][1]["callback_data"], f"review:reject:{CID}")

    def test_approve_requires_confirmation_and_current_role(self):
        preview = self.run_update(cb(f"review:approve:{CID}"))
        self.assertIn("Подтвердите", preview[0]["text"])
        self.assertFalse(self.calls)
        actual = self.run_update(cb(f"review:confirm:{CID}"))
        self.assertIn("одобрена", actual[0]["text"])
        self.assertEqual(self.calls, [("decide", 77, CID, "approved", None)])

    def test_reject_requires_written_reason(self):
        request = self.run_update(cb(f"review:reject:{CID}"))
        self.assertIn("причину отказа", request[0]["text"])
        invalid = self.run_update(msg("x"))
        self.assertIn("от 3 до 2000", invalid[0]["text"])
        result = self.run_update(msg("Недопустимый контент"))
        self.assertIn("Решение", result[0]["text"])
        self.assertEqual(self.calls, [
            ("decide", 77, CID, "rejected", "Недопустимый контент"),
        ])

    def test_changes_request_requires_written_reason(self):
        self.run_update(cb(f"review:changes:{CID}"))
        self.run_update(msg("Уточните описание"))
        self.assertEqual(self.calls, [
            ("decide", 77, CID, "changes_requested", "Уточните описание"),
        ])

    def test_foreign_chat_cannot_moderate(self):
        result = self.run_update(cb(f"review:confirm:{CID}", uid=21, chat=77))
        self.assertIsNone(result)
        self.assertFalse(self.calls)

    def test_non_operator_is_not_authorized_by_bot_memory(self):
        def deny(*_args, **_kwargs):
            raise HTTPError("https://internal.example", 403, "Denied", {}, None)
        inbox = OperatorInbox(deny, deny, deny, clock=lambda: 100.0)
        result = response_for(msg("/moderation"), operator_inbox=inbox)
        self.assertIn("только оператору", result[0]["text"])

    def test_unsubscribe(self):
        result = self.run_update(msg("/moderation_off"))
        self.assertEqual(self.calls, [("off", 77)])
        self.assertIn("отключены", result[0]["text"])

    def test_expired_comment_cannot_moderate(self):
        current = [100.]
        inbox = OperatorInbox(self.inbox.queue_loader,
                              self.inbox.decider, self.inbox.unsubscribe,
                              clock=lambda: current[0])
        response_for(cb(f"review:reject:{CID}"), operator_inbox=inbox)
        current[0] = 701.
        self.assertIsNone(response_for(msg("Причина"), operator_inbox=inbox))
        self.assertFalse(self.calls)

    def test_notify_only_new_submission_and_role_filtered_recipients(self):
        def api(route, payload):
            if route == "campaign-drafts/submit":
                return {"moderation_status": "pending", "verified_now": True}
            if route == "operators/recipients":
                return [55, 99]
            self.fail("unexpected route")
        with patch("bot.main._internal_api", side_effect=api), \
             patch("bot.main.telegram_call") as send, \
             patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:test"}):
            submit_campaign_draft(77, CID)
        self.assertEqual(send.call_count, 2)
        self.assertEqual([c.args[2]["chat_id"] for c in send.call_args_list], [55, 99])

    def test_idempotent_submission_does_not_resend_notifications(self):
        with patch("bot.main._internal_api", return_value={
            "moderation_status": "pending", "verified_now": False,
        }) as api, patch("bot.main.telegram_call") as send:
            submit_campaign_draft(77, CID)
        self.assertEqual(api.call_count, 1)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
