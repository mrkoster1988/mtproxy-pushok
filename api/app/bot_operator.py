"""Закрытые маршруты очереди модерации и opt-in уведомлений для Telegram.

Доступ выдаётся только доверенному боту, после отдельной проверки
Telegram user ID по ролям admin/operator в PostgreSQL.
"""
import os
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from .bot_routes import _verify_bot_secret

router = APIRouter()


class OperatorIdentity(BaseModel):
    telegram_user_id: int = Field(gt=0, le=9223372036854775807)


class OperatorQueueRequest(OperatorIdentity):
    subscribe: bool = False


class OperatorQueueItem(BaseModel):
    campaign_id: UUID
    channel_title: str
    channel_url: str
    tariff_name: str
    price_kopecks: int
    currency: str


class OperatorQueueResponse(BaseModel):
    subscribed: bool
    pending: list[OperatorQueueItem]


class OperatorDecision(OperatorIdentity):
    campaign_id: UUID
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str | None = Field(default=None, max_length=2000)


class OperatorDecisionResult(BaseModel):
    campaign_id: UUID
    moderation_status: str
    payment_status: str


def _operator(connection, telegram_id: int):
    row = connection.execute(
        """
        SELECT id FROM users
        WHERE telegram_user_id = %s AND role IN ('admin', 'operator')
        """,
        (telegram_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="operator_role_required")
    return row["id"]


@router.post("/api/v1/internal/bot/operators/queue",
             response_model=OperatorQueueResponse, include_in_schema=False)
def operator_queue(
    payload: OperatorQueueRequest, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> OperatorQueueResponse:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            actor_id = _operator(connection, payload.telegram_user_id)
            if payload.subscribe:
                connection.execute(
                    """
                    INSERT INTO bot_operator_subscriptions (operator_user_id)
                    VALUES (%s)
                    ON CONFLICT (operator_user_id) DO NOTHING
                    """,
                    (actor_id,),
                )
            subscribed = connection.execute(
                "SELECT 1 FROM bot_operator_subscriptions WHERE operator_user_id = %s",
                (actor_id,),
            ).fetchone() is not None
            rows = connection.execute(
                """
                SELECT c.id AS campaign_id, sc.title AS channel_title,
                       sc.telegram_url AS channel_url,
                       c.tariff_name_snapshot AS tariff_name,
                       c.price_kopecks_snapshot AS price_kopecks,
                       btrim(c.currency_snapshot) AS currency
                FROM campaigns c
                JOIN sponsor_channels sc ON sc.id = c.channel_id
                WHERE c.moderation_status = 'pending' AND c.archived_at IS NULL
                ORDER BY c.created_at ASC, c.id ASC
                LIMIT 5
                """,
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return OperatorQueueResponse(subscribed=subscribed,
                                 pending=[OperatorQueueItem(**r) for r in rows])


@router.post("/api/v1/internal/bot/operators/unsubscribe",
             include_in_schema=False)
def operator_unsubscribe(
    payload: OperatorIdentity, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            connection.execute(
                """
                DELETE FROM bot_operator_subscriptions
                WHERE operator_user_id IN (
                    SELECT id FROM users WHERE telegram_user_id = %s
                )
                """,
                (payload.telegram_user_id,),
            )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"subscribed": False}


@router.post("/api/v1/internal/bot/operators/recipients",
             response_model=list[int], include_in_schema=False)
def operator_recipients(
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> list[int]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
            rows = connection.execute(
                """
                SELECT u.telegram_user_id
                FROM bot_operator_subscriptions s
                JOIN users u ON u.id = s.operator_user_id
                WHERE u.role IN ('admin', 'operator') AND u.telegram_user_id > 0
                ORDER BY s.subscribed_at ASC
                LIMIT 5
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [int(r[0]) for r in rows]


@router.post("/api/v1/internal/bot/operators/decide",
             response_model=OperatorDecisionResult, include_in_schema=False)
def operator_decision(
    payload: OperatorDecision, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> OperatorDecisionResult:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    comment = payload.comment.strip() if payload.comment else None
    if payload.decision != "approved" and not comment:
        raise HTTPException(status_code=422, detail="moderation_comment_required")
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            actor_id = _operator(connection, payload.telegram_user_id)
            row = connection.execute(
                """
                UPDATE campaigns
                SET moderation_status = %s,
                    payment_status = CASE WHEN %s = 'approved'
                                          THEN 'awaiting_payment'
                                          ELSE payment_status END,
                    moderator_comment = %s, updated_at = now()
                WHERE id = %s AND moderation_status = 'pending' AND archived_at IS NULL
                RETURNING id AS campaign_id, moderation_status, payment_status
                """,
                (payload.decision, payload.decision, comment, payload.campaign_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=409, detail="campaign_not_pending")
            connection.execute(
                """
                INSERT INTO audit_events
                    (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'campaign_moderated',
                        jsonb_build_object('decision', %s::text, 'source', 'telegram_bot'))
                """,
                (actor_id, payload.campaign_id, payload.decision),
            )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return OperatorDecisionResult(**row)
