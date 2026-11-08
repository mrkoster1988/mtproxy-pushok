"""Внутренний read-only API для доверенного Telegram-бота.

Этот маршрут не использует публичные сессии и не доступен без отдельного
высокоэнтропийного секрета BOT_INTERNAL_SECRET. Доступ только к
кампаниям указанного Telegram user ID; секрет проверяется до чтения БД.
"""
import hmac
import os
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.errors import UniqueViolation
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
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
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
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


# MVP: один действующий тариф. Любое изменение цены требует отдельного
# решения владельца, поэтому не продаём его автоматически по другой цене.
DAY_TARIFF_ID = UUID("00000000-0000-4000-8000-000000000001")


class BotDraftCreate(BaseModel):
    telegram_user_id: int = Field(gt=0, le=9223372036854775807)
    channel_username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
    request_key: UUID

    @field_validator("channel_username")
    @classmethod
    def prohibit_reserved_telegram_paths(cls, value: str) -> str:
        if value.lower() in {"joinchat", "addstickers", "share", "proxy", "socks", "login"}:
            raise ValueError("Это служебный путь Telegram, не имя канала")
        return value


class BotDraftResult(BaseModel):
    campaign_id: UUID
    channel_url: str
    tariff_name: str
    price_kopecks: int
    currency: str
    moderation_status: str


@router.post(
    "/api/v1/internal/bot/campaign-drafts",
    response_model=BotDraftResult,
    include_in_schema=False,
)
def create_bot_campaign_draft(
    payload: BotDraftCreate,
    response: Response,
    x_pushok_bot_secret: str | None = Header(default=None),
) -> BotDraftResult:
    """Создать только черновик с устойчивым ключом запроса — без списания денег.

    При повторной доставке Telegram update операция возвращает тот же ID.
    Защита от повторного заказа действует в базе, не в памяти бота.
    """
    _verify_bot_secret(x_pushok_bot_secret)
    response.headers["Cache-Control"] = "no-store"
    channel_url = "https://t.me/" + payload.channel_username.lower()
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
        ) as connection:
            tariff = connection.execute(
                """
                SELECT id, name, duration_seconds, price_kopecks, currency
                FROM tariffs
                WHERE id = %s AND is_active = TRUE
                  AND duration_seconds = 86400 AND price_kopecks = 30000
                  AND btrim(currency) = 'RUB'
                """,
                (DAY_TARIFF_ID,),
            ).fetchone()
            if tariff is None:
                raise HTTPException(status_code=409, detail="bot_tariff_not_available")

            connection.execute(
                """
                INSERT INTO users (telegram_user_id)
                VALUES (%s)
                ON CONFLICT (telegram_user_id) DO NOTHING
                """,
                (payload.telegram_user_id,),
            )
            user = connection.execute(
                "SELECT id FROM users WHERE telegram_user_id = %s",
                (payload.telegram_user_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO sponsor_channels (owner_user_id, telegram_url, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (owner_user_id, telegram_url) DO NOTHING
                """,
                (user["id"], channel_url, "@" + payload.channel_username.lower()),
            )
            channel = connection.execute(
                """
                SELECT id FROM sponsor_channels
                WHERE owner_user_id = %s AND telegram_url = %s
                """,
                (user["id"], channel_url),
            ).fetchone()

            created = connection.execute(
                """
                INSERT INTO campaigns (
                    advertiser_user_id, channel_id, tariff_id,
                    tariff_name_snapshot, duration_seconds_snapshot,
                    price_kopecks_snapshot, currency_snapshot, bot_request_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bot_request_key)
                    WHERE bot_request_key IS NOT NULL
                DO NOTHING
                RETURNING id
                """,
                (
                    user["id"], channel["id"], tariff["id"],
                    tariff["name"], tariff["duration_seconds"],
                    tariff["price_kopecks"], tariff["currency"],
                    payload.request_key,
                ),
            ).fetchone()
            if created is not None:
                campaign_id = created["id"]
            else:
                existing = connection.execute(
                    """
                    SELECT id, advertiser_user_id, channel_id FROM campaigns
                    WHERE bot_request_key = %s
                    """,
                    (payload.request_key,),
                ).fetchone()
                if (
                    existing is None
                    or existing["advertiser_user_id"] != user["id"]
                    or existing["channel_id"] != channel["id"]
                ):
                    raise HTTPException(status_code=409, detail="bot_request_key_conflict")
                campaign_id = existing["id"]
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="bot_draft_conflict") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return BotDraftResult(
        campaign_id=campaign_id,
        channel_url=channel_url,
        tariff_name=tariff["name"],
        price_kopecks=tariff["price_kopecks"],
        currency=tariff["currency"].strip(),
        moderation_status="draft",
    )
