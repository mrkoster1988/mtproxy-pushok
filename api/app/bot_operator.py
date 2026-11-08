"""Закрытые маршруты очереди модерации для Telegram-операторов.

BOT_INTERNAL_SECRET — доступ только для процесса бота.
Telegram ID отправителя берётся процессом бота из подписанного Telegram update;
роль operator/admin проверяется по PostgreSQL на каждом вызове.
"""
import os
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from psycopg.rows import dict_row

from .bot_routes import _verify_bot_secret

router = APIRouter(prefix="/api/v1/internal/bot/operator", include_in_schema=False)


class OperatorIdentity(BaseModel):
    telegram_user_id: int = Field(gt=0, le=9223372036854775807)


class OperatorDecision(OperatorIdentity):
    campaign_id: UUID
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment")
    @classmethod
    def strip_comment(cls, value):
        return value.strip() if value is not None else None


class CampaignId(OperatorIdentity):
    campaign_id: UUID


class OperatorItem(BaseModel):
    id: UUID
    channel_title: str
    channel_url: str
    price_kopecks_snapshot: int
    currency_snapshot: str
    created_at: datetime


class DecisionResult(BaseModel):
    campaign_id: UUID
    moderation_status: str
    payment_status: str


class DecisionEvent(BaseModel):
    campaign_id: UUID
    channel_title: str
    decision: str
    created_at: datetime


def _operator(connection, telegram_user_id: int):
    found = connection.execute(
        """
        SELECT id, role FROM users
        WHERE telegram_user_id = %s AND role IN ('operator', 'admin')
        """,
        (telegram_user_id,),
    ).fetchone()
    if found is None:
        raise HTTPException(status_code=403, detail="operator_role_required")
    return found


def _db():
    return psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
    )


def _safe_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


@router.post("/queue", response_model=list[OperatorItem])
def moderation_queue(
    payload: OperatorIdentity,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
):
    _verify_bot_secret(x_pushok_bot_secret)
    _safe_response(response)
    try:
        with _db() as conn:
            _operator(conn, payload.telegram_user_id)
            rows = conn.execute(
                """
                SELECT c.id, sc.title AS channel_title, sc.telegram_url AS channel_url,
                       c.price_kopecks_snapshot, c.currency_snapshot, c.created_at
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                WHERE c.moderation_status = 'pending'
                ORDER BY c.created_at, c.id LIMIT 5
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [OperatorItem(**{**row, "currency_snapshot": row["currency_snapshot"].strip()}) for row in rows]


@router.post("/review", response_model=DecisionResult)
def review_campaign(
    payload: OperatorDecision,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
):
    _verify_bot_secret(x_pushok_bot_secret)
    _safe_response(response)
    if payload.decision != "approved" and not payload.comment:
        raise HTTPException(status_code=422, detail="moderation_comment_required")
    try:
        with _db() as conn:
            operator = _operator(conn, payload.telegram_user_id)
            updated = conn.execute(
                """
                UPDATE campaigns SET
                    moderation_status = %s,
                    payment_status = CASE WHEN %s = 'approved'
                                          THEN 'awaiting_payment'
                                          ELSE payment_status END,
                    moderator_comment = %s, updated_at = now()
                WHERE id = %s AND moderation_status = 'pending'
                  AND payment_status = 'not_required'
                  AND placement_status = 'not_scheduled'
                RETURNING id, moderation_status, payment_status
                """,
                (payload.decision, payload.decision,
                 payload.comment, payload.campaign_id),
            ).fetchone()
            if updated is None:
                raise HTTPException(status_code=409, detail="campaign_not_pending")
            conn.execute(
                """
                INSERT INTO audit_events
                    (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'campaign_moderated',
                        jsonb_build_object('decision', %s::text,
                                           'comment', %s::text,
                                           'source', 'telegram_bot'::text))
                """,
                (operator["id"], payload.campaign_id,
                 payload.decision, payload.comment),
            )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return DecisionResult(
        campaign_id=updated["id"],
        moderation_status=updated["moderation_status"],
        payment_status=updated["payment_status"],
    )


@router.post("/history", response_model=list[DecisionEvent])
def moderation_history(
    payload: OperatorIdentity,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
):
    _verify_bot_secret(x_pushok_bot_secret)
    _safe_response(response)
    try:
        with _db() as conn:
            operator = _operator(conn, payload.telegram_user_id)
            rows = conn.execute(
                """
                SELECT a.campaign_id, sc.title AS channel_title,
                       a.details_json->>'decision' AS decision, a.created_at
                FROM audit_events AS a
                JOIN campaigns AS c ON c.id = a.campaign_id
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                WHERE a.event_type = 'campaign_moderated'
                  AND a.actor_user_id = %s
                ORDER BY a.created_at DESC LIMIT 10
                """,
                (operator["id"],),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [DecisionEvent(**row) for row in rows]


@router.post("/notifications", response_model=list[OperatorItem])
def pending_notifications(
    payload: OperatorIdentity,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
):
    """Вернуть до 5 ожидающих заявок без отметки доставки этому оператору."""
    _verify_bot_secret(x_pushok_bot_secret)
    _safe_response(response)
    try:
        with _db() as conn:
            operator = _operator(conn, payload.telegram_user_id)
            rows = conn.execute(
                """
                SELECT c.id, sc.title AS channel_title, sc.telegram_url AS channel_url,
                       c.price_kopecks_snapshot, c.currency_snapshot, c.created_at
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                LEFT JOIN bot_operator_notifications AS n
                  ON n.campaign_id = c.id AND n.operator_user_id = %s
                WHERE c.moderation_status = 'pending' AND n.campaign_id IS NULL
                ORDER BY c.created_at, c.id LIMIT 5
                """,
                (operator["id"],),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [OperatorItem(**{**row, "currency_snapshot": row["currency_snapshot"].strip()}) for row in rows]


@router.post("/notifications/ack")
def acknowledge_notification(
    payload: CampaignId,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
):
    _verify_bot_secret(x_pushok_bot_secret)
    _safe_response(response)
    try:
        with _db() as conn:
            operator = _operator(conn, payload.telegram_user_id)
            saved = conn.execute(
                """
                INSERT INTO bot_operator_notifications(campaign_id, operator_user_id)
                SELECT c.id, %s FROM campaigns AS c WHERE c.id = %s
                ON CONFLICT (campaign_id, operator_user_id) DO NOTHING
                RETURNING campaign_id
                """,
                (operator["id"], payload.campaign_id),
            ).fetchone()
            if saved is None:
                # Повторное ACK допустимо, но неизвестный campaign_id — нет.
                exists = conn.execute(
                    "SELECT 1 FROM campaigns WHERE id = %s",
                    (payload.campaign_id,),
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="campaign_not_found")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"acknowledged": True}
