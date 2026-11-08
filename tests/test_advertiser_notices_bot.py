"""Приватная отправка решений модератора — без сети и Telegram-токенов."""
import unittest
from urllib.error import HTTPError
from uuid import UUID

from bot.advertiser_notices import deliver_advertiser_notices, render_advertiser_notice

CID = "00000000-0000-4000-8000-000000000173"
NID = "00000000-0000-4000-8000-000000000171"
TOKEN = "00000000-0000-4000-8000-000000000172"


def notice(decision="approved", comment=None):
    return {"notice_id": NID, "claim_token": TOKEN, "campaign_id": CID,
            "telegram_user_id": 2345, "decision": decision, "comment": comment}


class AdvertiserNoticeBotTests(unittest.TestCase):
    def test_all_three_templates(self):
        for status, phrase in (
            ("approved", "ожидание оплаты"),
            ("rejected", "отклонена"),
            ("changes_requested", "исправления"),
        ):
            with self.subTest(status=status):
                text = render_advertiser_notice(notice(status, "<b>Причина</b>"))
                self.assertIn(phrase, text)
                self.assertIn("/campaigns", text)
        self.assertIn("<b>Причина</b>",
                      render_advertiser_notice(notice("rejected", "<b>Причина</b>")))

    def test_delivery_sends_only_to_claimed_user_then_acks(self):
        calls, sent = [], []
        def api(route, payload):
            calls.append((route, payload))
            if route.endswith("claim"):
                return [notice("changes_requested", "Нужна ссылка")]
            if route.endswith("finish"):
                return {"acknowledged": True}
            self.fail("Unexpected route")
        deliver_advertiser_notices(api, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["chat_id"], 2345)
        self.assertIn("Нужна ссылка", sent[0]["text"])
        self.assertEqual(sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                         "campaigns")
        self.assertEqual(calls[1], (
            "advertisers/outbox/finish",
            {"notice_id": NID, "claim_token": TOKEN, "permanent_failure": False},
        ))

    def test_network_failure_does_not_ack(self):
        calls = []
        def api(route, payload):
            calls.append(route)
            return [notice()]
        def failure(_):
            raise RuntimeError("private token in URL")
        deliver_advertiser_notices(api, failure)
        self.assertEqual(calls, ["advertisers/outbox/claim"])

    def test_telegram_403_marks_blocked_chat_permanently(self):
        calls = []
        def api(route, payload):
            calls.append((route, payload))
            return [notice()] if route.endswith("claim") else {"acknowledged": True}
        def blocked(_):
            raise HTTPError("https://api.telegram.org/botSECRET", 403,
                            "Forbidden", {}, None)
        deliver_advertiser_notices(api, blocked)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1][1]["permanent_failure"])

    def test_stale_ack_keeps_notice_for_retry(self):
        sent = []
        def api(route, payload):
            if route.endswith("claim"):
                return [notice()]
            raise HTTPError("https://internal.example", 409, "expired", {}, None)
        deliver_advertiser_notices(api, sent.append)
        self.assertEqual(len(sent), 1)

    def test_invalid_recipient_is_never_sent_or_acked(self):
        item = notice()
        item["telegram_user_id"] = -9
        calls = []
        def api(route, payload):
            calls.append(route)
            return [item]
        deliver_advertiser_notices(api, lambda _: self.fail("must not send"))
        self.assertEqual(calls, ["advertisers/outbox/claim"])

    def test_limits_batch_size(self):
        items = [notice() for _ in range(8)]
        count = []
        def api(route, payload):
            return items if route.endswith("claim") else {"acknowledged": True}
        deliver_advertiser_notices(api, count.append)
        self.assertEqual(len(count), 5)

    def test_untrusted_comment_is_bounded(self):
        payload = render_advertiser_notice(notice("rejected", "x" * 100000))
        self.assertLess(len(payload), 1600)
        self.assertNotIn("x" * 1101, payload)


if __name__ == "__main__":
    unittest.main()
