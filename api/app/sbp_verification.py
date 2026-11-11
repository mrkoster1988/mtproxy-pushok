"""Fail-closed validation of a verified SBP provider payment.

Input MUST be a fresh payment object fetched server-to-server from the
acquirer using authenticated credentials. A webhook body or redirect query
MUST NOT be used as the payment object. This module makes no network calls,
database updates or automatic sponsor changes.
"""
from decimal import Decimal, InvalidOperation
from typing import Mapping, Any


class SBPVerificationError(ValueError):
    """Provider response does not match the order snapshot."""


def validate_sbp_identity(
    payment: Mapping[str, Any],
    *,
    expected_payment_id: str,
    expected_campaign_id: str,
    expected_kopecks: int,
    expected_test: bool | None = None,
) -> str:
    """Return provider payment ID only for an exact, captured SBP payment.

    The caller must separately enforce DB uniqueness/idempotency and verify
    the authenticated provider GET response before using this function.
    """
    if not isinstance(payment, Mapping):
        raise SBPVerificationError("invalid_payment_object")
    if not isinstance(expected_payment_id, str) or not expected_payment_id:
        raise SBPVerificationError("invalid_expected_payment_id")
    if not isinstance(expected_campaign_id, str) or not expected_campaign_id:
        raise SBPVerificationError("invalid_expected_campaign_id")
    if type(expected_kopecks) is not int or expected_kopecks <= 0:
        raise SBPVerificationError("invalid_expected_amount")

    if payment.get("id") != expected_payment_id:
        raise SBPVerificationError("payment_id_mismatch")
    if expected_test is not None and payment.get('test') is not expected_test:
        raise SBPVerificationError('payment_test_mode_mismatch')
    method = payment.get("payment_method")
    if not isinstance(method, Mapping) or method.get("type") != "sbp":
        raise SBPVerificationError("payment_method_mismatch")
    metadata = payment.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("campaign_id") != expected_campaign_id:
        raise SBPVerificationError("campaign_mismatch")
    amount = payment.get("amount")
    if not isinstance(amount, Mapping) or amount.get("currency") != "RUB":
        raise SBPVerificationError("currency_mismatch")
    value = amount.get("value")
    if not isinstance(value, str):
        raise SBPVerificationError("invalid_amount")
    try:
        rub = Decimal(value)
    except (InvalidOperation, ValueError):
        raise SBPVerificationError("invalid_amount") from None
    if not rub.is_finite() or rub.as_tuple().exponent != -2:
        raise SBPVerificationError("invalid_amount_precision")
    if rub * 100 != expected_kopecks:
        raise SBPVerificationError("amount_mismatch")
    return expected_payment_id


def verify_sbp_payment(payment: Mapping[str, Any], *, expected_payment_id: str,
                       expected_campaign_id: str, expected_kopecks: int) -> str:
    """Только окончательный захваченный платёж может подтверждать заказ."""
    identifier = validate_sbp_identity(payment, expected_payment_id=expected_payment_id,
        expected_campaign_id=expected_campaign_id, expected_kopecks=expected_kopecks)
    if payment.get('status') != 'succeeded' or payment.get('paid') is not True:
        raise SBPVerificationError('payment_not_succeeded')
    if payment.get('captured_at') is None:
        raise SBPVerificationError('payment_not_captured')
    refunded = payment.get('refunded_amount')
    if refunded is not None and (not isinstance(refunded, Mapping)
        or refunded.get('currency') != 'RUB' or refunded.get('value') != '0.00'):
        raise SBPVerificationError('payment_has_refund')
    return identifier
