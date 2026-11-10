"""Проверки двухшагового оформления черновика — без Telegram API и денег."""
import unittest

from bot.wizard import DraftWizard, extract_channel_username
from bot.main import response_for


def message(telegram_id, text, chat_id=None, group=False):
    return {"message": {
        "chat": {"id": telegram_id if chat_id is None else chat_id,
                 "type": "group" if group else "private"},
        "from": {"id": telegram_id},
        "text": text,
    }}


def callback(telegram_id, data, chat_id=None):
    return {"callback_query": {
        "id": "cb-" + data,
        "data": data,
        "from": {"id": telegram_id},
        "message": {
            "chat": {"id": telegram_id if chat_id is None else chat_id,
                     "type": "private"}
        },
    }}


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.created = []
        def creator(user_id, username, key):
            self.created.append((user_id, username, key))
            return {"campaign_id": "1234"}
        self.wizard = DraftWizard(creator, clock=lambda: self.now)

    def handle(self, update):
        return response_for(update, wizard=self.wizard)

    def test_channel_name_parser(self):
        for sample in ("@Pushok_Test", "https://t.me/Pushok_Test",
                       "t.me/Pushok_Test", "https://telegram.me/Pushok_Test/"):
            with self.subTest(sample=sample):
                self.assertEqual(extract_channel_username(sample), "pushok_test")
        for sample in ("https://evil.t.me/pushok_test", "https://t.me/abc?x=1",
                       "@a", "hello there", "https://t.me/joinchat",
                       "https://t.me/abc/channel", "https://t.me/a%2Fb"):
            with self.subTest(sample=sample):
                self.assertIsNone(extract_channel_username(sample))

    def test_entire_draft_flow_one_creation(self):
        start = self.handle(message(42, "/advertise"))
        self.assertIn("Шаг 1", start[0]["text"])
        confirm = self.handle(message(42, "https://t.me/MyGreatChannel"))
        self.assertIn("@mygreatchannel", confirm[0]["text"])
        self.assertEqual(confirm[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "draft:confirm")
        complete = self.handle(callback(42, "draft:confirm"))
        self.assertIn("Черновик создан", complete[0]["text"])
        self.assertIn("Тариф:", complete[0]["text"])
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0][:2], (42, "mygreatchannel"))
        repeat = self.handle(callback(42, "draft:confirm"))
        self.assertEqual(len(self.created), 1)
        self.assertIn("Начните", repeat[0]["text"])

    def test_cancel_does_not_create_campaign(self):
        self.handle(callback(42, "advertise"))
        self.handle(message(42, "@MyChannel"))
        reply = self.handle(callback(42, "draft:cancel"))
        self.assertIn("отменено", reply[0]["text"])
        self.handle(callback(42, "draft:confirm"))
        self.assertEqual(self.created, [])

    def test_invalid_user_and_group_cannot_progress(self):
        self.assertIsNone(self.wizard.handle(message(42, "/advertise", chat_id=43)))
        self.assertIsNone(self.wizard.handle(message(42, "/advertise", group=True)))
        self.assertEqual(self.wizard.sessions, {})

    def test_expiry_discards_pending_draft(self):
        self.handle(message(42, "/advertise"))
        self.handle(message(42, "@MyChannel"))
        self.now += 601
        reply = self.handle(callback(42, "draft:confirm"))
        self.assertIn("Начните", reply[0]["text"])
        self.assertFalse(self.created)

    def test_backend_error_preserves_idempotency_key(self):
        calls = []
        def fail(_user, _channel, request_key):
            calls.append(request_key)
            if len(calls) == 1:
                raise RuntimeError("internal secret")
            return {"campaign_id": "100"}
        wizard = DraftWizard(fail, clock=lambda: self.now)
        response_for(message(42, "/advertise"), wizard=wizard)
        response_for(message(42, "@MyChannel"), wizard=wizard)
        first = response_for(callback(42, "draft:confirm"), wizard=wizard)
        self.assertIn("Не получилось", first[0]["text"])
        self.assertNotIn("internal secret", first[0]["text"])
        second = response_for(callback(42, "draft:confirm"), wizard=wizard)
        self.assertIn("Черновик создан", second[0]["text"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_two_people_cannot_mix_pending_drafts(self):
        self.handle(message(42, "/advertise"))
        self.handle(message(43, "/advertise"))
        self.handle(message(42, "@ChannelA"))
        self.handle(message(43, "@ChannelB"))
        forged = self.handle(callback(42, "draft:confirm", chat_id=43))
        self.assertIsNone(forged)
        self.assertEqual(len(self.created), 0)
        self.handle(callback(43, "draft:confirm"))
        self.handle(callback(42, "draft:confirm"))
        self.assertEqual([(user, channel) for user, channel, _ in self.created],
                         [(43, "channelb"), (42, "channela")])
        self.assertNotEqual(self.created[0][2], self.created[1][2])

    def test_unconfigured_instance_does_not_take_orders(self):
        wizard = DraftWizard(self.wizard.creator, enabled=False, clock=lambda: self.now)
        result = response_for(message(42, "/advertise"), wizard=wizard)
        self.assertIn("пока недоступно", result[0]["text"])
        self.assertFalse(wizard.sessions)


if __name__ == "__main__":
    unittest.main()
