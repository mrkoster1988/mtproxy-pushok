"""v0.049: оплата Stars в боте — имитация Telegram и внутреннего API."""
import unittest
from uuid import UUID
from bot.main import process_stars_update, response_for

CID = UUID("00000000-0000-4000-8000-000000000997")
ATT = UUID("00000000-0000-4000-8000-000000000998")


class StarsBotTests(unittest.TestCase):
    def test_buttons_only_for_payable_campaign(self):
        campaign = {
            "campaign_id": str(CID), "channel_title": "Тестовый канал",
            "tariff_name_snapshot": "1 день", "price_kopecks_snapshot": 30000,
            "currency_snapshot": "RUB", "moderation_status": "approved",
            "payment_status": "awaiting_payment", "placement_status": "not_scheduled",
            "stars_price_xtr": 180,
        }
        reply = response_for({"message": {
            "from": {"id": 42}, "chat": {"type": "private", "id": 42},
            "text": "/campaigns",
        }}, campaign_loader=lambda user: [campaign])
        buttons = reply[0]["reply_markup"]["inline_keyboard"]
        self.assertTrue(any(b.get("callback_data") == "stars:" + str(CID)
                            for row in buttons for b in row))
        campaign["stars_price_xtr"] = None
        second = response_for({"message": {
            "from": {"id": 42}, "chat": {"type": "private", "id": 42},
            "text": "/campaigns",
        }}, campaign_loader=lambda user: [campaign])
        self.assertFalse(any(b.get("callback_data", "").startswith("stars:")
                             for row in second[0]["reply_markup"]["inline_keyboard"] for b in row))

    def test_invoice_is_xtr_without_provider_token(self):
        calls = []
        def send(_, method, payload, **kwargs):
            calls.append((method, payload, kwargs))
        def api(route, payload):
            self.assertEqual(route, "stars/prepare")
            self.assertEqual(payload["telegram_user_id"], 42)
            return {"stars_amount": 180, "title": "MTProxy Pushok",
                    "description": "Размещение", "invoice_payload": "stars:" + str(ATT)}
        handled = process_stars_update({"callback_query": {
            "id": "callback", "data": "stars:" + str(CID),
            "from": {"id": 42}, "message": {"chat": {"type": "private", "id": 42}},
        }}, "fake", internal_api=api, send=send)
        self.assertTrue(handled)
        self.assertEqual([x[0] for x in calls], ["answerCallbackQuery", "sendInvoice"])
        payload = calls[1][1]
        self.assertEqual(payload["currency"], "XTR")
        self.assertEqual(payload["prices"][0]["amount"], 180)
        self.assertNotIn("provider_token", payload)

    def test_foreign_actor_cannot_order(self):
        calls = []
        handled = process_stars_update({"callback_query": {
            "id": "callback", "data": "stars:" + str(CID),
            "from": {"id": 9}, "message": {"chat": {"type": "private", "id": 42}},
        }}, "fake", internal_api=lambda *x: self.fail("forbidden"),
             send=lambda *x, **y: calls.append((x, y)))
        self.assertTrue(handled)
        self.assertEqual(calls, [])

    def test_precheckout_mismatch_is_denied(self):
        sent = []
        ok = process_stars_update({"pre_checkout_query": {
            "id": "pc-1", "from": {"id": 42},
            "invoice_payload": "stars:" + str(ATT),
            "currency": "XTR", "total_amount": 180,
        }}, "fake", internal_api=lambda *x: {"ok": False},
             send=lambda *args, **kwargs: sent.append((args, kwargs)))
        self.assertTrue(ok)
        self.assertEqual(sent[0][0][1], "answerPreCheckoutQuery")
        self.assertFalse(sent[0][0][2]["ok"])
        self.assertEqual(sent[0][1]["timeout"], 4)

    def test_success_requires_durable_settlement(self):
        sent = []
        paid = {"message": {
            "from": {"id": 42}, "chat": {"id": 42, "type": "private"},
            "successful_payment": {
                "invoice_payload": "stars:" + str(ATT),
                "currency": "XTR", "total_amount": 180,
                "telegram_payment_charge_id": "charge-1",
            },
        }}
        self.assertTrue(process_stars_update(
            paid, "fake", internal_api=lambda *x: {"status": "applied"},
            send=lambda *args, **kw: sent.append(args)))
        self.assertEqual(sent[0][1], "sendMessage")
        self.assertIn("подтверждена", sent[0][2]["text"])
        with self.assertRaises(RuntimeError):
            process_stars_update(paid, "fake",
                                 internal_api=lambda *x: {"status": "unknown"},
                                 send=lambda *x, **kw: self.fail("not persisted"))

    def test_paysupport_available(self):
        r = response_for({"message": {
            "chat": {"id": 42, "type": "private"}, "text": "/paysupport",
        }})
        self.assertIn("Помощь с оплатой Stars", r[0]["text"])


if __name__ == "__main__":
    unittest.main()
