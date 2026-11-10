"""Транзакционная сверка ранее созданного СБП-платежа.

Вызывать только из доверенного серверного обработчика с открытым соединением
PostgreSQL, управляемым внешним with conn: для commit/rollback.
Webhook — лишь повод вызвать сверку: его тело НЕ является доказательством.
Этот модуль не выставляет счета и не включает MTProxy.
"""
from uuid import UUID

from .sbp_provider import get_sbp_payment
from .sbp_verification import verify_sbp_payment, SBPVerificationError


class SBPReconcileError(ValueError):
    """Попытка не допускает автоматического зачёта — нужна сверка."""


def reconcile_sbp_attempt(conn, attempt_id: UUID, *,
                          fetch_payment=get_sbp_payment) -> dict:
    """Сверить с авторитетным GET и атомарно применить один платёж.

    Требуется транзакционное соединение. Сетевой вызов может поднять ошибку:
    вызывающий код обязан откатить транзакцию, не подтверждать оплату.
    """
    if not isinstance(attempt_id, UUID):
        raise SBPReconcileError("invalid_attempt_id")
    row = conn.execute(
        """SELECT a.id, a.campaign_id, a.provider_payment_id, a.status,
                  a.amount, a.currency, a.provider, a.payment_channel,
                  c.payment_status, c.moderation_status, c.placement_status,
                  c.advertiser_user_id
           FROM payment_attempts AS a
           JOIN campaigns AS c ON c.id = a.campaign_id
           WHERE a.id = %s
           FOR UPDATE OF a, c""",
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise SBPReconcileError("attempt_not_found")
    if row["provider"] != "sbp" or row["payment_channel"] != "external_web":
        raise SBPReconcileError("attempt_not_sbp")
    provider_id = row["provider_payment_id"]
    if not isinstance(provider_id, str) or not provider_id:
        raise SBPReconcileError("provider_payment_id_missing")
    if row["status"] not in ("pending", "succeeded"):
        raise SBPReconcileError("attempt_not_payable")
    if row["currency"].strip() != "RUB" or type(row["amount"]) is not int:
        raise SBPReconcileError("invalid_payment_snapshot")
    # Даже при повторном webhook проверяем текущее состояние у провайдера.
    verified_id = verify_sbp_payment(
        fetch_payment(provider_id),
        expected_payment_id=provider_id,
        expected_campaign_id=str(row["campaign_id"]),
        expected_kopecks=row["amount"],
    )
    if row["status"] == "succeeded":
        if row["payment_status"] != "verified":
            raise SBPReconcileError("payment_state_conflict")
        return {"status": "already_processed", "campaign_id": str(row["campaign_id"])}
    if (row["moderation_status"] != "approved"
            or row["payment_status"] != "awaiting_payment"
            or row["placement_status"] != "not_scheduled"):
        raise SBPReconcileError("campaign_not_payable")
    # Внешняя транзакция откатывает обе операции при любом сбое.
    updated = conn.execute(
        """UPDATE campaigns SET payment_status='verified', updated_at=now()
           WHERE id=%s AND payment_status='awaiting_payment'
           RETURNING id""", (row["campaign_id"],),
    ).fetchone()
    if updated is None:
        raise SBPReconcileError("campaign_changed")
    conn.execute(
        """UPDATE payment_attempts
           SET status='succeeded', settled_at=now()
           WHERE id=%s AND status='pending' AND provider_payment_id=%s""",
        (attempt_id, verified_id),
    )
    conn.execute(
        """INSERT INTO audit_events(actor_user_id, campaign_id, event_type, details_json)
           VALUES(%s,%s,'sbp_payment_verified',
                  jsonb_build_object('attempt_id',%s::text,'provider','sbp'))""",
        (row["advertiser_user_id"], row["campaign_id"], str(attempt_id)),
    )
    return {"status": "applied", "campaign_id": str(row["campaign_id"])}
