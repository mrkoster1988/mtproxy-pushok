"""Приватная очередь Telegram-уведомлений рекламодателям.

Владелец заявки определяется триггером PostgreSQL, не параметром запроса.
Lease и токен защищают ACK от старых или конкурирующих обработчиков.
"""
import os
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel
from psycopg.rows import dict_row

from .bot_routes import _verify_bot_secret

router = APIRouter()


class AdvertiserNotice(BaseModel):
    notice_id: UUID
    claim_token: UUID
    telegram_user_id: int
    campaign_id: UUID
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str | None = None


class NoticeFinish(BaseModel):
    notice_id: UUID
    claim_token: UUID
    permanent_failure: bool = False


@router.post(
    "/api/v1/internal/bot/advertisers/outbox/claim",
    response_model=list[AdvertiserNotice],
    include_in_schema=False,
)
def claim_advertiser_notices(
    response: Response, x_pushok_bot_secret: str | None = Header(default=None),
) -> list[AdvertiserNotice]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            rows = connection.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM bot_advertiser_notice_outbox
                    WHERE delivered_at IS NULL AND abandoned_at IS NULL
                      AND next_attempt_at <= now()
                      AND (lease_until IS NULL OR lease_until < now())
                    ORDER BY next_attempt_at, created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 5
                ), claimed AS (
                    UPDATE bot_advertiser_notice_outbox AS n
                    SET attempts = n.attempts + 1,
                        claim_token = gen_random_uuid(),
                        lease_until = now() + interval '90 seconds',
                        next_attempt_at = now()
                            + interval '2 minutes' * LEAST(n.attempts + 1, 30)
                    FROM due
                    WHERE n.id = due.id
                    RETURNING n.id AS notice_id, n.claim_token,
                              n.campaign_id, n.recipient_user_id,
                              n.decision, n.comment_snapshot AS comment
                )
                SELECT c.notice_id, c.claim_token, c.campaign_id,
                       u.telegram_user_id, c.decision, c.comment
                FROM claimed AS c
                JOIN users AS u ON u.id = c.recipient_user_id
                WHERE u.telegram_user_id > 0
                """,
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [AdvertiserNotice(**row) for row in rows]


@router.post(
    "/api/v1/internal/bot/advertisers/outbox/finish",
    include_in_schema=False,
)
def finish_advertiser_notice(
    payload: NoticeFinish, response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            finished = connection.execute(
                """
                UPDATE bot_advertiser_notice_outbox
                SET delivered_at = CASE WHEN NOT %s THEN now() ELSE delivered_at END,
                    abandoned_at = CASE WHEN %s THEN now() ELSE abandoned_at END,
                    lease_token = NULL, lease_until = NULL
                WHERE id = %s AND claim_token = %s AND lease_until >= now()
                  AND delivered_at IS NULL AND abandoned_at IS NULL
                RETURNING id
                """,
                (
                    payload.permanent_failure, payload.permanent_failure,
                    payload.notice_id, payload.claim_token,
                ),
            ).fetchone()
            if finished is None:
                raise HTTPException(status_code=409, detail="notice_claim_expired")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"acknowledged": True}
