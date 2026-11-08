"""Проверки кнопки модерации после сохранения рекламного черновика."""
import unittest
from urllib.error import HTTPError
from uuid import UUID

from bot.main import response_for
from bot.wizard import DraftWizard

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000197")


def callback(sender=42, chat=42, action=None, chat_type="private"):
    return {"callback_query": {
        "id": "callback-verification",
        "data": action or "submit:" + str(CAMPAIGN),
        "from": {"id": sender},
        "message": {"chat": {"id": chat, "type": chat_type}},
    }}


class BotSubmissionTests(unittest.TestCase):
    def test_successful_submission_uses_sender_identity(self):
        calls = []
        def send(sender, campaign_id):
            calls.append((sender, campaign_id))
            return {"moderation_status": "pending"}
        response = response_for(callback(), submitter=send)
        self.assertEqual(calls, [(42, CAMPAIGN)])
        self.assertIn("отправлен на модерацию", response[0]["text"])
        self.assertEqual(response[1], "callback-verification")

    def test_other_actor_cannot_submit(self):
        calls = []
        result = response_for(callback(sender=7, chat=42),
                              submitter=lambda *args: calls.append(args))
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_group_is_not_eligible(self):
        self.assertIsNone(response_for(callback(chat_type="supergroup"),
                                       submitter=lambda *_: self.fail("called")))

    def test_bad_campaign_id_is_rejected(self):
        result = response_for(callback(action="submit:hello"),
                              submitter=lambda *_: self.fail("called"))
        self.assertIn("Не удалось определить заявку", result[0]["text"])

    def test_missing_api_does_not_claim_success(self):
        result = response_for(callback())
        self.assertIn("недоступна", result[0]["text"])
        self.assertNotIn("отправлен", result[0]["text"])

    def test_verification_rejection_has_actionable_help(self):
        def denied(*_):
            raise HTTPError("https://api.telegram.org/botSECRET", 409,
                            "Conflict", {}, None)
        result = response_for(callback(), submitter=denied)
        self.assertIn("@mtproxy_pushok_bot", result[0]["text"])
        self.assertNotIn("SECRET", result[0]["text"])

    def test_backend_failure_does_not_expose_secret(self):
        def failed(*_):
            raise RuntimeError("BOT_INTERNAL_SECRET has leaked")
        result = response_for(callback(), submitter=failed)
        self.assertIn("временно недоступна", result[0]["text"])
        self.assertNotIn("BOT_INTERNAL_SECRET", result[0]["text"])

    def test_wizard_sends_real_uuid_for_manual_submission(self):
        wizard = DraftWizard(
            lambda *_: {"campaign_id": str(CAMPAIGN)},
            clock=lambda: 100.0,
        )
        message = lambda text: {"message": {
            "chat": {"type": "private", "id": 42},
            "from": {"id": 42}, "text": text,
        }}
        response_for(message("/advertise"), wizard=wizard)
        response_for(message("@ExampleChannel"), wizard=wizard)
        confirm = {"callback_query": {
            "id": "confirm", "data": "draft:confirm", "from": {"id": 42},
            "message": {"chat": {"type": "private", "id": 42}},
        }}
        saved = response_for(confirm, wizard=wizard)
        button = saved[0]["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["callback_data"], "submit:" + str(CAMPAIGN))
        self.assertIn("Проверить канал", button["text"])


if __name__ == "__main__":
    unittest.main()
