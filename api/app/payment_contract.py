"""Контракты двух платежных каналов MTProxy Pushok.

ВАЖНО: это валидация и безопасная модель, а не интеграция с эквайрингом.
Telegram Stars внутри бота; СБП — только на самостоятельном внешнем сайте.
Ни один метод не делает вызова к провайдеру и не помечает кампанию оплаченной.
"""
from typing import Any


class PaymentContractError(ValueError):
    """Нарушение подтверждённого сервером платёжного контракта."""


CHANNELS = {"telegram_stars": ("telegram_bot", "XTR"),
            "sbp": ("external_web", "RUB")}


def payment_quote(
    *,
    provider: str,
    channel: str,
    price_kopecks: int,
    stars_amount: int | None = None,
) -> dict[str, Any]:
    """Выдать только расчётную схему; не выставлять счёт и не менять БД."""
    if provider not in CHANNELS:
        raise PaymentContractError("payment_provider_not_supported")
    expected_channel, currency = CHANNELS[provider]
    if channel != expected_channel:
        raise PaymentContractError("payment_channel_not_allowed")
    if type(price_kopecks) is not int or price_kopecks <= 0:
        raise PaymentContractError("invalid_rub_price")
    if provider == "telegram_stars":
        if type(stars_amount) is not int or stars_amount <= 0:
            raise PaymentContractError("stars_price_not_configured")
        amount = stars_amount
    else:
        amount = price_kopecks
    return {
        "provider": provider, "payment_channel": channel,
        "currency": currency, "amount": amount,
        "rub_price_kopecks_reference": price_kopecks,
        "ready_to_charge": False,
        "payment_confirmed": False,
        "automatically_activate_sponsor": False,
    }


def validate_settlement(
    *,
    provider: str,
    expected_provider: str,
    expected_currency: str,
    expected_amount: int,
    observed_currency: str,
    observed_amount: int,
    provider_payment_id: str,
    existing_payment_id: str | None,
    status: str,
) -> str:
    """После ПРОВЕРКИ подлинности у провайдера защитить повторную доставку.

    ВАЖНО: не проверяет подлинность webhook/payment; вызывать только после
    Telegram successful_payment или запроса GET к эквайрингу.
    """
    if provider != expected_provider or provider not in CHANNELS:
        raise PaymentContractError("payment_provider_mismatch")
    if expected_currency != CHANNELS[provider][1] or observed_currency != expected_currency:
        raise PaymentContractError("payment_currency_mismatch")
    if type(expected_amount) is not int or expected_amount <= 0:
        raise PaymentContractError("payment_expected_amount_invalid")
    if type(observed_amount) is not int or observed_amount != expected_amount:
        raise PaymentContractError("payment_amount_mismatch")
    if not isinstance(provider_payment_id, str) or not 1 <= len(provider_payment_id) <= 255:
        raise PaymentContractError("payment_charge_id_invalid")
    if status == "succeeded":
        if provider_payment_id == existing_payment_id:
            return "already_processed"
        raise PaymentContractError("payment_already_settled_with_different_charge")
    if status not in ("created", "pending"):
        raise PaymentContractError("payment_not_payable")
    if existing_payment_id is not None and existing_payment_id != provider_payment_id:
        raise PaymentContractError("payment_charge_mismatch")
    return "mark_succeeded_after_provider_verification"
