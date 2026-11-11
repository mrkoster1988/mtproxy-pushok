"""v0.049: критические отрицательные тесты платежей Stars."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main
from app.bot_stars import parse_attempt, eligible

SECRET = "b" * 64
CID = UUID("00000000-0000-4000-8000-000000000991")
ATT = UUID("00000000-0000-4000-8000-000000000992")
UID = UUID("00000000-0000-4000-8000-000000000993")
H = {"X-Pushok-Bot-Secret": SECRET}


def fake_db(*rows):
    connection = MagicMock()
    connection.__enter__.return_value.execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row}) for row in rows
    ]
    return connection


def row():
    return {"id": ATT, "campaign_id": CID, "amount": 180, "currency": "XTR",
            "status": "pending", "provider_payment_id": None,
            "payer_telegram_user_id": 42, "telegram_user_id": 42,
            "moderation_status": "approved", "payment_status": "awaiting_payment",
            "placement_status": "not_scheduled", "advertiser_user_id": UID,
            "precheckout_approved_at": "approved", "precheckout_query_id": "pc-1",
            "expires_at": "soon", "tariff_is_active": True}


class BotStarsAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.secret = patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": SECRET, "PUSHOK_STARS_PAYMENTS_ENABLED": "1"
        })
        self.secret.start()

    def tearDown(self):
        self.client.close()
        self.secret.stop()

    def test_stars_invoices_disabled_without_database_write(self):
        with patch.dict(os.environ, {"PUSHOK_STARS_PAYMENTS_ENABLED": "0"}):
            with patch("app.bot_stars.psycopg.connect") as db:
                response = self.client.post("/api/v1/internal/bot/stars/prepare",
                    json={"telegram_user_id": 42, "campaign_id": str(CID)}, headers=H)
                self.assertEqual(response.status_code, 409, response.text)
                db.assert_not_called()
                refusal = self.client.post("/api/v1/internal/bot/stars/precheckout",
                    json={"query_id": "pc", "telegram_user_id": 42,
                          "invoice_payload": "stars:" + str(ATT),
                          "currency": "XTR", "total_amount": 180}, headers=H)
                self.assertEqual(refusal.status_code, 200)
                self.assertFalse(refusal.json()["ok"])
                db.assert_not_called()

    def test_auth_before_database(self):
        with patch("app.bot_stars.psycopg.connect") as db:
            r = self.client.post("/api/v1/internal/bot/stars/prepare",
                    json={"telegram_user_id": 42, "campaign_id": str(CID)})
        self.assertEqual(r.status_code, 401)
        db.assert_not_called()

    def test_payload_is_strict(self):
        self.assertEqual(parse_attempt("stars:" + str(ATT)), ATT)
        self.assertIsNone(parse_attempt("stars:invalid"))
        self.assertIsNone(parse_attempt("sbp:" + str(ATT)))

    def test_eligible_binds_amount_and_owner(self):
        self.assertTrue(eligible(row(), 42, "XTR", 180))
        self.assertFalse(eligible(row(), 41, "XTR", 180))
        self.assertFalse(eligible(row(), 42, "RUB", 180))
        self.assertFalse(eligible(row(), 42, "XTR", 181))
        self.assertFalse(eligible({**row(), "payment_status": "verified"}, 42, "XTR", 180))

    def test_unconfigured_price_denies_invoice(self):
        campaign = {"moderation_status": "approved", "payment_status": "awaiting_payment",
                    "placement_status": "not_scheduled", "currency_snapshot": "RUB",
                    "stars_price_xtr": None, "is_active": True}
        db = fake_db(campaign)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/prepare",
                 json={"telegram_user_id": 42, "campaign_id": str(CID)}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)

    def test_invoice_amount_snapshot_from_settings(self):
        campaign = {"moderation_status": "approved", "payment_status": "awaiting_payment",
                    "placement_status": "not_scheduled", "currency_snapshot": "RUB",
                    "stars_price_xtr": 180, "is_active": True}
        db = fake_db(campaign, None, None, None, None, {"id": ATT, "amount": 180})
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/prepare",
                 json={"telegram_user_id": 42, "campaign_id": str(CID)}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["stars_amount"], 180)
        self.assertEqual(r.json()["invoice_payload"], "stars:" + str(ATT))
        self.assertEqual(db.__enter__.return_value.execute.call_args_list[5].args[1],
                         (CID, 180, 42, False))

    def test_precheckout_mismatch_denies_without_writes(self):
        db = fake_db(row())
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/precheckout",
                 json={"query_id": "pc-1", "telegram_user_id": 42,
                       "invoice_payload": "stars:" + str(ATT),
                       "currency": "XTR", "total_amount": 999}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(db.__enter__.return_value.execute.call_count, 1)

    def test_valid_precheckout_records_query(self):
        db = fake_db({**row(), "precheckout_query_id": None},
                     {"valid": True}, {"id": ATT})
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/precheckout",
                 json={"query_id": "pc-1", "telegram_user_id": 42,
                       "invoice_payload": "stars:" + str(ATT),
                       "currency": "XTR", "total_amount": 180}, headers=H)
        self.assertTrue(r.json()["ok"])
        self.assertIn("precheckout_approved_at",
                      db.__enter__.return_value.execute.call_args_list[2].args[0])
        self.assertIn("RETURNING id", db.__enter__.return_value.execute.call_args_list[2].args[0])

    def test_successful_receipt_updates_campaign(self):
        receipt = {"id": 9, "handling_status": "received", "total_amount": 180,
                   "currency": "XTR", "payer_telegram_user_id": 42,
                   "attempt_id": None}
        db = fake_db(None, receipt, row(), {"id": ATT}, {"id": CID}, None, None)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/settle",
                 json={"telegram_user_id": 42, "invoice_payload": "stars:" + str(ATT),
                       "currency": "XTR", "total_amount": 180,
                       "telegram_payment_charge_id": "charge-1"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "applied")
        queries = [c.args[0] for c in db.__enter__.return_value.execute.call_args_list]
        self.assertTrue(any("UPDATE campaigns SET payment_status='verified'" in x for x in queries))

    def test_precheckout_update_must_affect_pending_attempt(self):
        db = fake_db({**row(), "precheckout_query_id": None},
                     {"valid": True}, None)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/precheckout",
                 json={"query_id": "pc-1", "telegram_user_id": 42,
                       "invoice_payload": "stars:" + str(ATT),
                       "currency": "XTR", "total_amount": 180}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["ok"])
        self.assertIn("RETURNING id", db.__enter__.return_value.execute.call_args_list[-1].args[0])

    def test_settlement_requires_updated_attempt_before_campaign_or_audit(self):
        receipt = {"id": 9, "handling_status": "received", "total_amount": 180,
                   "currency": "XTR", "payer_telegram_user_id": 42,
                   "attempt_id": None}
        db = fake_db(None, receipt, row(), None)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/settle",
                json={"telegram_user_id": 42, "invoice_payload": "stars:" + str(ATT),
                      "currency": "XTR", "total_amount": 180,
                      "telegram_payment_charge_id": "charge-1"}, headers=H)
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["detail"], "stars_attempt_changed_retry")
        queries = [c.args[0] for c in db.__enter__.return_value.execute.call_args_list]
        self.assertIn("RETURNING id", queries[-1])
        self.assertFalse(any("UPDATE campaigns" in q or "stars_payment_verified" in q for q in queries))

    def test_settlement_requires_updated_campaign_before_receipt_audit(self):
        receipt = {"id": 9, "handling_status": "received", "total_amount": 180,
                   "currency": "XTR", "payer_telegram_user_id": 42,
                   "attempt_id": None}
        db = fake_db(None, receipt, row(), {"id": ATT}, None)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/settle",
                json={"telegram_user_id": 42, "invoice_payload": "stars:" + str(ATT),
                      "currency": "XTR", "total_amount": 180,
                      "telegram_payment_charge_id": "charge-1"}, headers=H)
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["detail"], "stars_campaign_changed_retry")
        queries = [c.args[0] for c in db.__enter__.return_value.execute.call_args_list]
        self.assertIn("RETURNING id", queries[-1])
        self.assertFalse(any("handling_status='applied'" in q or
                             "stars_payment_verified" in q for q in queries))

    def test_unknown_receipt_is_saved_for_review(self):
        receipt = {"id": 9, "handling_status": "received", "total_amount": 180,
                   "currency": "XTR", "payer_telegram_user_id": 42,
                   "attempt_id": None}
        db = fake_db(None, receipt, None)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/settle",
                 json={"telegram_user_id": 42, "invoice_payload": "unknown",
                       "currency": "XTR", "total_amount": 180,
                       "telegram_payment_charge_id": "charge-1"}, headers=H)
        self.assertEqual(r.json()["status"], "requires_review")
        queries = [c.args[0] for c in db.__enter__.return_value.execute.call_args_list]
        self.assertTrue(any("handling_status='requires_review'" in x for x in queries))
        self.assertFalse(any("UPDATE campaigns" in x for x in queries))

    def test_duplicate_charge_is_safe(self):
        receipt = {"id": 9, "handling_status": "applied", "total_amount": 180,
                   "currency": "XTR", "payer_telegram_user_id": 42, "attempt_id": ATT}
        db = fake_db(None, receipt)
        with patch("app.bot_stars.psycopg.connect", return_value=db):
            r = self.client.post("/api/v1/internal/bot/stars/settle",
                 json={"telegram_user_id": 42, "invoice_payload": "stars:" + str(ATT),
                       "currency": "XTR", "total_amount": 180,
                       "telegram_payment_charge_id": "charge-1"}, headers=H)
        self.assertEqual(r.json()["status"], "already_processed")
        self.assertEqual(db.__enter__.return_value.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
