"""Telegram Stars: закрытый API, записи только после сообщений Bot API.

Отправить счёт ≠ получить деньги; pre-checkout ≠ получить деньги.
Только successful_payment, доставленный Telegram боту через getUpdates,
может завершить оплату. СБП обрабатывается отдельно.
"""
import os
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from .bot_routes import _verify_bot_secret
from .payment_contract import validate_settlement, PaymentContractError
from .payment_gate import stars_invoices_enabled

router = APIRouter()
CHECKOUT_ERROR = "Заказ или цена изменились. Откройте «Мои кампании» и получите новый счёт."


class StarsPrepareInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    campaign_id: UUID


class StarsPrepareOutput(BaseModel):
    invoice_payload: str
    stars_amount: int
    title: str
    description: str


class StarsPrecheckoutInput(BaseModel):
    query_id: str = Field(min_length=1, max_length=255)
    telegram_user_id: int = Field(gt=0)
    invoice_payload: str = Field(min_length=1, max_length=80)
    currency: str
    total_amount: int


class StarsPrecheckoutOutput(BaseModel):
    ok: bool
    error_message: str | None = None


class StarsReceiptInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    invoice_payload: str = Field(min_length=1, max_length=80)
    currency: str
    total_amount: int
    telegram_payment_charge_id: str = Field(min_length=1, max_length=255)


class StarsReceiptOutput(BaseModel):
    status: str
    campaign_id: UUID | None = None


def parse_attempt(payload: str) -> UUID | None:
    if not isinstance(payload, str) or not payload.startswith("stars:"):
        return None
    try:
        identifier = UUID(payload[6:])
    except (TypeError, ValueError, AttributeError):
        return None
    return identifier if payload == "stars:" + str(identifier) else None


def authorize(secret: str | None, response: Response) -> None:
    _verify_bot_secret(secret)
    response.headers["Cache-Control"] = "no-store"


def locked_attempt(conn, attempt_id: UUID):
    return conn.execute(
        """SELECT a.id, a.campaign_id, a.amount, a.currency, a.status,
                  a.provider_payment_id, a.payer_telegram_user_id, a.expires_at,
                  a.precheckout_query_id, a.precheckout_approved_at,
                  c.advertiser_user_id, c.moderation_status, c.payment_status,
                  c.placement_status, u.telegram_user_id,
                  t.is_active AS tariff_is_active
           FROM payment_attempts AS a
           JOIN campaigns AS c ON c.id=a.campaign_id
           JOIN users AS u ON u.id=c.advertiser_user_id
           JOIN tariffs AS t ON t.id=c.tariff_id
           WHERE a.id=%s AND a.provider='telegram_stars'
             AND a.payment_channel='telegram_bot'
           FOR UPDATE OF a, c""", (attempt_id,),
    ).fetchone()


def eligible(row, user_id: int, currency: str, amount: int) -> bool:
    return bool(
        row
        and row["payer_telegram_user_id"] == user_id
        and row["telegram_user_id"] == user_id
        and row["currency"].strip() == "XTR" and currency == "XTR"
        and type(amount) is int and amount == row["amount"]
        and row["moderation_status"] == "approved"
        and row["payment_status"] == "awaiting_payment"
        and row["placement_status"] == "not_scheduled"
    )


@router.post("/api/v1/internal/bot/stars/prepare",
             response_model=StarsPrepareOutput, include_in_schema=False)
def prepare_invoice(
    payload: StarsPrepareInput, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> StarsPrepareOutput:
    authorize(x_pushok_bot_secret, response)
    if not stars_invoices_enabled():
        raise HTTPException(409, "stars_checkout_disabled_until_acceptance")
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3,
                             row_factory=dict_row) as conn:
            c = conn.execute(
                """SELECT c.id, c.moderation_status, c.payment_status,
                          c.placement_status, c.currency_snapshot,
                          t.stars_price_xtr, t.is_active
                   FROM campaigns AS c
                   JOIN users AS u ON u.id=c.advertiser_user_id
                   JOIN tariffs AS t ON t.id=c.tariff_id
                   WHERE c.id=%s AND u.telegram_user_id=%s
                   FOR UPDATE OF c""",
                (payload.campaign_id, payload.telegram_user_id),
            ).fetchone()
            if c is None:
                raise HTTPException(404, "campaign_not_found")
            if (c["moderation_status"] != "approved"
                or c["payment_status"] != "awaiting_payment"
                or c["placement_status"] != "not_scheduled"
                or c["currency_snapshot"].strip() != "RUB"):
                raise HTTPException(409, "campaign_not_payable")
            stars = c["stars_price_xtr"]
            if not c["is_active"] or type(stars) is not int or not 1 <= stars <= 1000000:
                raise HTTPException(409, "stars_price_not_configured")
            if conn.execute(
                "SELECT 1 FROM payment_attempts WHERE campaign_id=%s AND status='succeeded' LIMIT 1",
                (payload.campaign_id,),
            ).fetchone():
                raise HTTPException(409, "campaign_already_paid")
            # Нельзя повторно выставлять одобренный к оплате счёт, пока он
            # не подтверждён или не разобран оператором: риск двойного списания.
            conn.execute(
                """UPDATE payment_attempts SET status='failed'
                   WHERE campaign_id=%s AND provider='telegram_stars'
                     AND status='pending' AND expires_at <= now()
                     AND precheckout_approved_at IS NULL""",
                (payload.campaign_id,),
            )
            prev = conn.execute(
                """SELECT id, amount, precheckout_approved_at
                   FROM payment_attempts
                   WHERE campaign_id=%s AND provider='telegram_stars'
                     AND status='pending'
                   ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (payload.campaign_id,),
            ).fetchone()
            if prev and prev["precheckout_approved_at"] is not None:
                raise HTTPException(409, "stars_payment_processing")
            if prev:
                attempt_id, amount = prev["id"], prev["amount"]
            else:
                if conn.execute("""SELECT 1 FROM payment_attempts WHERE campaign_id=%s
                                    AND provider='sbp' AND status IN ('created','pending') LIMIT 1""",
                                (payload.campaign_id,)).fetchone():
                    raise HTTPException(409, 'another_payment_in_progress')
                row = conn.execute(
                    """INSERT INTO payment_attempts(
                           campaign_id, provider, payment_channel, amount, currency,
                           status, payer_telegram_user_id, expires_at)
                       VALUES(%s,'telegram_stars','telegram_bot',%s,'XTR',
                              'pending',%s,now()+interval '10 minutes')
                       RETURNING id, amount""",
                    (payload.campaign_id, stars, payload.telegram_user_id),
                ).fetchone()
                attempt_id, amount = row["id"], row["amount"]
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(503, "database_unavailable") from exc
    return StarsPrepareOutput(
        invoice_payload="stars:" + str(attempt_id),
        stars_amount=amount, title="Реклама в MTProxy Pushok",
        description="Спонсорство по заявке " + str(payload.campaign_id)[:8],
    )


@router.post("/api/v1/internal/bot/stars/precheckout",
             response_model=StarsPrecheckoutOutput, include_in_schema=False)
def precheckout(
    payload: StarsPrecheckoutInput, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> StarsPrecheckoutOutput:
    authorize(x_pushok_bot_secret, response)
    if not stars_invoices_enabled():
        return StarsPrecheckoutOutput(ok=False, error_message="Оплата Stars ещё не включена.")
    attempt = parse_attempt(payload.invoice_payload)
    if attempt is None:
        return StarsPrecheckoutOutput(ok=False, error_message=CHECKOUT_ERROR)
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2,
                             row_factory=dict_row) as conn:
            a = locked_attempt(conn, attempt)
            if (not eligible(a, payload.telegram_user_id,
                             payload.currency, payload.total_amount)
                or a["status"] != "pending" or not a["tariff_is_active"]
                or a["expires_at"] is None):
                return StarsPrecheckoutOutput(ok=False, error_message=CHECKOUT_ERROR)
            valid = conn.execute(
                "SELECT %s::timestamptz > now() AS valid", (a["expires_at"],)
            ).fetchone()
            if not valid or not valid["valid"]:
                return StarsPrecheckoutOutput(ok=False, error_message=CHECKOUT_ERROR)
            if a["precheckout_query_id"] not in (None, payload.query_id):
                return StarsPrecheckoutOutput(ok=False, error_message=CHECKOUT_ERROR)
            if a["precheckout_query_id"] is None:
                updated = conn.execute(
                    """UPDATE payment_attempts
                       SET precheckout_query_id=%s, precheckout_approved_at=now()
                       WHERE id=%s AND status='pending'
                         AND precheckout_query_id IS NULL
                       RETURNING id""",
                    (payload.query_id, attempt),
                ).fetchone()
                if updated is None:
                    return StarsPrecheckoutOutput(ok=False, error_message=CHECKOUT_ERROR)
    except psycopg.Error:
        return StarsPrecheckoutOutput(
            ok=False, error_message="Сервис проверки временно недоступен."
        )
    return StarsPrecheckoutOutput(ok=True)


@router.post("/api/v1/internal/bot/stars/settle",
             response_model=StarsReceiptOutput, include_in_schema=False)
def settle(
    payload: StarsReceiptInput, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> StarsReceiptOutput:
    authorize(x_pushok_bot_secret, response)
    attempt = parse_attempt(payload.invoice_payload)
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3,
                             row_factory=dict_row) as conn:
            # Сначала фиксируем полученный charge, даже если заказ не удалось
            # сопоставить: иначе мы бы потеряли оплаченные деньги.
            conn.execute(
                """INSERT INTO stars_payment_receipts(
                       telegram_payment_charge_id, payer_telegram_user_id,
                       currency, total_amount)
                   VALUES(%s,%s,%s,%s)
                   ON CONFLICT(telegram_payment_charge_id) DO NOTHING""",
                (payload.telegram_payment_charge_id, payload.telegram_user_id,
                 payload.currency, payload.total_amount),
            )
            receipt = conn.execute(
                """SELECT id, handling_status, payer_telegram_user_id,
                          total_amount, currency, attempt_id
                   FROM stars_payment_receipts
                   WHERE telegram_payment_charge_id=%s FOR UPDATE""",
                (payload.telegram_payment_charge_id,),
            ).fetchone()
            if not receipt:
                raise HTTPException(503, "receipt_storage_unavailable")
            if (receipt["payer_telegram_user_id"] != payload.telegram_user_id
                or receipt["total_amount"] != payload.total_amount
                or receipt["currency"].strip() != payload.currency):
                return StarsReceiptOutput(status="requires_review")
            if receipt["handling_status"] == "applied":
                return StarsReceiptOutput(status="already_processed")
            if receipt["handling_status"] == "requires_review":
                return StarsReceiptOutput(status="requires_review")

            a = locked_attempt(conn, attempt) if attempt else None

            def unresolved(reason: str) -> StarsReceiptOutput:
                conn.execute(
                    """UPDATE stars_payment_receipts
                       SET handling_status='requires_review', reason_code=%s,
                           attempt_id=%s, processed_at=now() WHERE id=%s""",
                    (reason, attempt if a is not None else None, receipt["id"]),
                )
                return StarsReceiptOutput(status="requires_review")

            if not a:
                return unresolved("unknown_attempt")
            if not eligible(a, payload.telegram_user_id, payload.currency,
                            payload.total_amount):
                return unresolved("amount_user_or_status_mismatch")
            if a["precheckout_approved_at"] is None:
                return unresolved("precheckout_missing")
            try:
                decision = validate_settlement(
                    provider="telegram_stars", expected_provider="telegram_stars",
                    expected_currency="XTR", expected_amount=a["amount"],
                    observed_currency=payload.currency, observed_amount=payload.total_amount,
                    provider_payment_id=payload.telegram_payment_charge_id,
                    existing_payment_id=a["provider_payment_id"], status=a["status"],
                )
            except PaymentContractError:
                return unresolved("settlement_conflict")
            if decision == "already_processed":
                conn.execute(
                    """UPDATE stars_payment_receipts SET handling_status='applied',
                       attempt_id=%s, processed_at=now() WHERE id=%s""",
                    (attempt, receipt["id"]),
                )
                return StarsReceiptOutput(status="already_processed", campaign_id=a["campaign_id"])
            settled = conn.execute(
                """UPDATE payment_attempts SET status='succeeded',
                       provider_payment_id=%s, settled_at=now()
                   WHERE id=%s AND status='pending'
                     AND provider='telegram_stars' AND provider_payment_id IS NULL
                   RETURNING id""",
                (payload.telegram_payment_charge_id, attempt),
            ).fetchone()
            if settled is None:
                # Не признаём receipt применённым без обновлённой попытки.
                # HTTPException откатывает всю транзакцию, включая receipt.
                raise HTTPException(503, "stars_attempt_changed_retry")
            campaign_settled = conn.execute(
                """UPDATE campaigns SET payment_status='verified', updated_at=now()
                   WHERE id=%s AND payment_status='awaiting_payment'
                     AND moderation_status='approved' AND placement_status='not_scheduled'
                   RETURNING id""",
                (a["campaign_id"],),
            ).fetchone()
            if campaign_settled is None:
                # Нельзя оставить succeeded-попытку и неоплаченную кампанию.
                raise HTTPException(503, "stars_campaign_changed_retry")
            conn.execute(
                """UPDATE stars_payment_receipts SET handling_status='applied',
                       attempt_id=%s, processed_at=now() WHERE id=%s""",
                (attempt, receipt["id"]),
            )
            conn.execute(
                """INSERT INTO audit_events(actor_user_id, campaign_id,
                                                event_type, details_json)
                   VALUES(%s,%s,'stars_payment_verified',
                          jsonb_build_object('attempt_id',%s::text,'amount_xtr',%s::int))""",
                (a["advertiser_user_id"], a["campaign_id"], str(attempt), a["amount"]),
            )
            return StarsReceiptOutput(status="applied", campaign_id=a["campaign_id"])
    except HTTPException:
        raise
    except psycopg.Error as exc:
        # Бот должен повторить это Telegram update, если БД не приняла charge.
        raise HTTPException(503, "database_unavailable") from exc
