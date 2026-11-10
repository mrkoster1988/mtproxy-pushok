"""v0.047 — независимые от провайдеров проверки Stars/СБП."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.payment_contract import PaymentContractError, payment_quote, validate_settlement


class PaymentContractTests(unittest.TestCase):
    def test_stars_requires_separate_xtr_price(self):
        with self.assertRaises(PaymentContractError):
            payment_quote(provider="telegram_stars", channel="telegram_bot",
                          price_kopecks=30000)
        q = payment_quote(provider="telegram_stars", channel="telegram_bot",
                          price_kopecks=30000, stars_amount=100)
        self.assertEqual((q["currency"], q["amount"]), ("XTR", 100))
        self.assertFalse(q["ready_to_charge"])
        self.assertFalse(q["payment_confirmed"])

    def test_sbp_uses_exact_rub_kopecks(self):
        q = payment_quote(provider="sbp", channel="external_web", price_kopecks=30000)
        self.assertEqual((q["currency"], q["amount"]), ("RUB", 30000))
        self.assertFalse(q["automatically_activate_sponsor"])

    def test_sbp_in_bot_is_forbidden(self):
        for channel in ("telegram_bot", "telegram_mini_app"):
            with self.subTest(channel=channel), self.assertRaises(PaymentContractError):
                payment_quote(provider="sbp", channel=channel, price_kopecks=30000)

    def test_stars_external_sbp_channel_forbidden(self):
        with self.assertRaises(PaymentContractError):
            payment_quote(provider="telegram_stars", channel="external_web",
                          price_kopecks=30000, stars_amount=100)

    def test_invalid_prices_not_accepted(self):
        for amount in (None, 0, -1, True, 300.5, "30000"):
            with self.subTest(amount=amount), self.assertRaises(PaymentContractError):
                payment_quote(provider="sbp", channel="external_web", price_kopecks=amount)

    def test_unknown_provider_rejected(self):
        with self.assertRaises(PaymentContractError):
            payment_quote(provider="card", channel="external_web", price_kopecks=30000)

    def base_event(self):
        return dict(provider="telegram_stars", expected_provider="telegram_stars",
                    expected_currency="XTR", expected_amount=100,
                    observed_currency="XTR", observed_amount=100,
                    provider_payment_id="charge_123", existing_payment_id=None,
                    status="pending")

    def test_settlement_requires_correct_price_currency_method(self):
        for field, value in (("observed_amount", 101),
                             ("observed_currency", "RUB"),
                             ("provider", "sbp"), ("provider_payment_id", "")):
            with self.subTest(field=field), self.assertRaises(PaymentContractError):
                validate_settlement(**{**self.base_event(), field: value})

    def test_settlement_is_idempotent_for_same_charge(self):
        event = {**self.base_event(), "status": "succeeded",
                 "existing_payment_id": "charge_123"}
        self.assertEqual(validate_settlement(**event), "already_processed")

    def test_settlement_rejects_new_charge_for_paid_order(self):
        event = {**self.base_event(), "status": "succeeded",
                 "existing_payment_id": "charge_old"}
        with self.assertRaises(PaymentContractError):
            validate_settlement(**event)

    def test_new_success_requires_verified_provider_event(self):
        result = validate_settlement(**self.base_event())
        self.assertEqual(result, "mark_succeeded_after_provider_verification")

    def test_refunded_cannot_be_repaid_by_old_webhook(self):
        with self.assertRaises(PaymentContractError):
            validate_settlement(**{**self.base_event(), "status": "refunded"})

    def test_schema_does_not_confuse_stars_with_rubles(self):
        sql = (ROOT / "database/013_payment_attempts.sql").read_text()
        self.assertIn("telegram_stars", sql)
        self.assertIn("payment_channel = 'telegram_bot'", sql)
        self.assertIn("payment_channel = 'external_web'", sql)
        self.assertIn("payment_attempts_one_success_per_campaign", sql)
        self.assertIn("UNIQUE (provider, provider_payment_id)", sql)


if __name__ == "__main__":
    unittest.main()
