"""Закрытая транзакционная очередь уведомлений операторов.

Контроллер-бот забирает максимум пять сообщений и подтверждает каждое только
после успешного Telegram sendMessage. Ошибки повторяются не чаще раза в 2 минуты.
"""
import os
from uuid import UUID

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel
from psycopg.rows import dict_row

from .bot_routes import _verify_bot_secret

router = APIRouter(prefix="/api/v1/internal/bot/operators/outbox",
                   include_in_schema=False)


class Notice(BaseModel):
    notice_id: UUID
    campaign_id: UUID
    telegram_user_id: int
    channel_title: str | None = None
    channel_url: str | None = None
    advertiser_name: str | None = None
    advertiser_username: str | None = None


class NoticeAck(BaseModel):
    notice_id: UUID


@router.post("/claim", response_model=list[Notice])
def claim_notifications(
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> list[Notice]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            rows = connection.execute(
                """
                SELECT n.id AS notice_id, n.campaign_id, u.telegram_user_id,
                       sc.title AS channel_title, sc.telegram_url AS channel_url,
                       concat_ws(' ', advertiser.first_name, advertiser.last_name) AS advertiser_name,
                       advertiser.username AS advertiser_username
                FROM bot_operator_notice_outbox AS n
                JOIN bot_operator_subscriptions AS s
                  ON s.operator_user_id = n.operator_user_id
                JOIN users AS u ON u.id = n.operator_user_id
                JOIN campaigns AS c ON c.id = n.campaign_id
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                JOIN users AS advertiser ON advertiser.id = c.advertiser_user_id
                WHERE n.delivered_at IS NULL
                  AND c.moderation_status = 'pending' AND c.archived_at IS NULL
                  AND u.role IN ('admin', 'operator') AND u.blocked_at IS NULL
                  AND u.telegram_user_id > 0
                  AND (n.last_attempt_at IS NULL
                    OR n.last_attempt_at < now() - interval '2 minutes')
                ORDER BY n.created_at, n.id
                LIMIT 5
                FOR UPDATE OF n SKIP LOCKED
                """
            ).fetchall()
            if rows:
                connection.execute(
                    """
                    UPDATE bot_operator_notice_outbox
                    SET last_attempt_at = now(), attempts = attempts + 1
                    WHERE id = ANY(%s::uuid[]) AND delivered_at IS NULL
                    """,
                    ([row["notice_id"] for row in rows],),
                )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [Notice(**row) for row in rows]


@router.post("/ack")
def acknowledge_notification(
    payload: NoticeAck,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            updated = connection.execute(
                """
                UPDATE bot_operator_notice_outbox
                SET delivered_at = now()
                WHERE id = %s AND delivered_at IS NULL
                RETURNING id
                """,
                (payload.notice_id,),
            ).fetchone()
            if updated is None:
                exists = connection.execute(
                    "SELECT 1 FROM bot_operator_notice_outbox WHERE id = %s",
                    (payload.notice_id,),
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="notification_not_found")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"acknowledged": True}
