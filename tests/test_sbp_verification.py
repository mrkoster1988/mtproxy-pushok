"""SBP payment matching is deterministic and fails closed."""
import pytest
from api.app.sbp_verification import verify_sbp_payment, SBPVerificationError


def valid():
    return {
        "id": "payment-123", "status": "succeeded", "paid": True,
        "captured_at": "2026-11-10T18:00:00Z",
        "payment_method": {"type": "sbp"},
        "metadata": {"campaign_id": "42"},
        "amount": {"value": "300.00", "currency": "RUB"},
    }


def check(payment, **overrides):
    args = dict(expected_payment_id="payment-123",
                expected_campaign_id="42", expected_kopecks=30000)
    args.update(overrides)
    return verify_sbp_payment(payment, **args)


def test_valid_confirmed_provider_object():
    assert check(valid()) == "payment-123"


@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("status", "pending"), ("paid", False),
    ("captured_at", None), ("payment_method", {"type": "bank_card"}),
    ("metadata", {"campaign_id": "43"}),
    ("amount", {"value": "299.99", "currency": "RUB"}),
    ("amount", {"value": "300.00", "currency": "XTR"}),
    ("amount", {"value": "NaN", "currency": "RUB"}),
    ("amount", {"value": "300.001", "currency": "RUB"}),
    ("amount", {"value": 300.0, "currency": "RUB"}),
])
def test_rejects_mismatches(field, value):
    payment = valid()
    payment[field] = value
    with pytest.raises(SBPVerificationError):
        check(payment)


def test_missing_or_untrusted_object_rejected():
    with pytest.raises(SBPVerificationError):
        check({"status": "succeeded"})
    with pytest.raises(SBPVerificationError):
        check(valid(), expected_kopecks=True)
    with pytest.raises(SBPVerificationError):
        check(valid(), expected_campaign_id="another")
