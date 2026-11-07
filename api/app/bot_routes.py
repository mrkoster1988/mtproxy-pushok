"""Внутренний read-only API для доверенного Telegram-бота.

Этот маршрут не использует публичные сессии и не доступен без отдельного
высокоэнтропийного секрета BOT_INTERNAL_SECRET. Доступ только к
кампаниям указанного Telegram user ID; секрет проверяется до чтения БД.
"""
import hmac
import os
from datetime import datetime

import psycopg
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

router = APIRouter()


class BotCampaignLookup(BaseModel):
    telegram_user_id: int = Field(gt=0)


class BotCampaignSummary(BaseModel):
    channel_title: str
    tariff_name_snapshot: str
    price_kopecks_snapshot: int
    currency_snapshot: str
    moderation_status: str
    payment_status: str
    placement_status: str
    scheduled_start_at: datetime | None = None


def _verify_bot_secret(provided: str | None) -> None:
    expected = os.getenv("BOT_INTERNAL_SECRET", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="bot_api_not_configured")
    if provided is None or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="bot_auth_required")


@router.post(
    "/api/v1/internal/bot/campaigns",
    response_model=list[BotCampaignSummary],
    include_in_schema=False,
)
def list_bot_campaigns(
    payload: BotCampaignLookup,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> list[BotCampaignSummary]:
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            rows = connection.execute(
                """
                SELECT sc.title AS channel_title,
                       c.tariff_name_snapshot, c.price_kopecks_snapshot,
                       c.currency_snapshot, c.moderation_status,
                       c.payment_status, c.placement_status,
                       c.scheduled_start_at
                FROM users AS u
                JOIN campaigns AS c ON c.advertiser_user_id = u.id
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                WHERE u.telegram_user_id = %s
                ORDER BY c.created_at DESC
                LIMIT 10
                """,
                (payload.telegram_user_id,),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [
        BotCampaignSummary(
            **{**row, "currency_snapshot": row["currency_snapshot"].strip()}
        )
        for row in rows
    ]
