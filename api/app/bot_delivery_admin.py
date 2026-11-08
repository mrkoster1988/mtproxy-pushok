"""Диагностика доставки уведомлений — только для администратора кабинета.

BOT_INTERNAL_SECRET проверяется до SQL; затем по Telegram from.id из
доверенного polling-бота повторно проверяется актуальная роль admin.
Ни номера получателей, ни комментарии модераторов не возвращаются.
"""
import os
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from .bot_routes import _verify_bot_secret

router = APIRouter()


class AdminIdentity(BaseModel):
    telegram_user_id: int = Field(gt=0, le=9223372036854775807)


class RetryNotice(AdminIdentity):
    notice_id: UUID


class DeliveryCounts(BaseModel):
    waiting: int
    in_flight: int
    delivered: int
    abandoned: int = 0


class DeliveryItem(BaseModel):
    notice_id: UUID
    campaign_id: UUID
    decision: str
    attempts: int
    status: str
    next_attempt_at: datetime


class DeliveryReport(BaseModel):
    advertiser: DeliveryCounts
    operator: DeliveryCounts
    recent_undelivered: list[DeliveryItem]


def _require_admin(connection, telegram_user_id):
    row = connection.execute(
        """SELECT id FROM users
           WHERE telegram_user_id = %s AND role = 'admin'""",
        (telegram_user_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="admin_role_required")
    return row["id"]


@router.post(
    "/api/v1/internal/bot/admin/delivery-report",
    response_model=DeliveryReport,
    include_in_schema=False,
)
def admin_delivery_report(
    payload: AdminIdentity,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> DeliveryReport:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row,
        ) as connection:
            _require_admin(connection, payload.telegram_user_id)
            advertiser = connection.execute(
                """
                SELECT
                    count(*) FILTER (
                        WHERE delivered_at IS NULL AND abandoned_at IS NULL
                          AND (lease_until IS NULL OR lease_until < now())
                    ) AS waiting,
                    count(*) FILTER (
                        WHERE delivered_at IS NULL AND abandoned_at IS NULL
                          AND lease_until >= now()
                    ) AS in_flight,
                    count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered,
                    count(*) FILTER (WHERE abandoned_at IS NOT NULL) AS abandoned
                FROM bot_advertiser_notice_outbox
                """
            ).fetchone()
            operator = connection.execute(
                """
                SELECT
                    count(*) FILTER (
                        WHERE delivered_at IS NULL AND
                          (last_attempt_at IS NULL
                           OR last_attempt_at < now() - interval '2 minutes')
                    ) AS waiting,
                    count(*) FILTER (
                        WHERE delivered_at IS NULL AND
                          last_attempt_at >= now() - interval '2 minutes'
                    ) AS in_flight,
                    count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered
                FROM bot_operator_notice_outbox
                """
            ).fetchone()
            recent = connection.execute(
                """
                SELECT id AS notice_id, campaign_id, decision, attempts,
                       CASE
                           WHEN abandoned_at IS NOT NULL THEN 'abandoned'
                           WHEN lease_until >= now() THEN 'in_flight'
                           ELSE 'waiting'
                       END AS status,
                       next_attempt_at
                FROM bot_advertiser_notice_outbox
                WHERE delivered_at IS NULL
                ORDER BY created_at DESC, id DESC
                LIMIT 8
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return DeliveryReport(
        advertiser=DeliveryCounts(**advertiser),
        operator=DeliveryCounts(**operator),
        recent_undelivered=[DeliveryItem(**item) for item in recent],
    )


@router.post(
    "/api/v1/internal/bot/admin/retry-advertiser-notice",
    include_in_schema=False,
)
def admin_retry_advertiser_notice(
    payload: RetryNotice,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    """Ускоряем лишь отложенную попытку, никогда не сбрасываем Telegram 403.

    Не трогаем отправленные, заблокированные и в данный момент арендованные
    сообщения. Запрос журналируется в той же транзакции.
    """
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row,
        ) as connection:
            actor_id = _require_admin(connection, payload.telegram_user_id)
            updated = connection.execute(
                """
                UPDATE bot_advertiser_notice_outbox
                SET next_attempt_at = now()
                WHERE id = %s AND delivered_at IS NULL
                  AND abandoned_at IS NULL
                  AND (lease_until IS NULL OR lease_until < now())
                  AND next_attempt_at > now()
                RETURNING campaign_id
                """,
                (payload.notice_id,),
            ).fetchone()
            if updated is None:
                raise HTTPException(status_code=409, detail="notice_not_retryable")
            connection.execute(
                """
                INSERT INTO audit_events
                    (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'advertiser_notice_retry_requested',
                        jsonb_build_object('notice_id', %s::uuid))
                """,
                (actor_id, updated["campaign_id"], payload.notice_id),
            )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"queued": True}
