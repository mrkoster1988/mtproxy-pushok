Warning: truncated output (original token count: 42074)
Total output lines: 3367

from .user_visibility import can_view_username, hide_username
from .telegram_environment import environment_name
from .payment_gate import sbp_reconciliation_enabled
import binascii
import hashlib
import hmac
import json
import os
import ipaddress
import re
from pathlib import Path
import secrets
import time
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone, date as Date, time as Time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg
from .license_errors import explain as explain_license_error
from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from psycopg.errors import CheckViolation, ExclusionViolation, InsufficientPrivilege, UniqueViolation
from psycopg.rows import dict_row
from .bot_routes import router as bot_router
from .bot_stars import router as bot_stars_router
from .bot_operator import router as operator_bot_router
from .bot_advertiser_notices import router as advertiser_notices_router
from .bot_outbox import router as bot_outbox_router
from .bot_delivery_admin import router as bot_delivery_admin_router
from .sponsor_planner import build_readiness, normalize_ad_tag, render_config_fragment
from .engine_adapters import recommend_engine, engine_catalog
from .core_readiness import core_readiness, core_diagnostic_plan
from .slot_calendar import slot_status
from .slot_holds import normalize_hold_window
from .placement_verification import validate_observation_window
from .placement_evidence import sanitize_image, store_image, read_verified_image, EvidenceReadError
from .placement_report import build_campaign_report, PUBLIC_EVENTS
from .report_export import render_campaign_csv
from .payment_gate import stars_invoices_enabled
from .launch_readiness import build_launch_readiness, MAX_AGE
from . import sbp_checkout, sbp_admin, stars_checkout
from .promotion_diagnostics import diagnostics as promotion_diagnostics
from .promotion_runtime import confirmation as promotion_confirmation, campaign_automation_status
from .miniapp_auth import validate_init_data
from .sbp_webhook import router as sbp_webhook_router
from .sbp_provider import SBPProviderError
from .sbp_reconcile import SBPReconcileError
from .sbp_verification import SBPVerificationError
from .channel_topics import Topic, label as topic_label

app = FastAPI(
    title="MTProxy Pushok API",
    version="1.126",
    description="API кабинета рекламодателя MTProxy Pushok.",
)
app.include_router(bot_router)
app.include_router(bot_stars_router)
app.include_router(operator_bot_router)
app.include_router(advertiser_notices_router)
app.include_router(bot_outbox_router)
app.include_router(bot_delivery_admin_router)
app.include_router(sbp_webhook_router)

DATABASE_URL = os.environ["DATABASE_URL"]
PAYMENT_PROOFS_DIR = os.getenv("PAYMENT_PROOFS_DIR", "/var/lib/mtproxy-pushok/payment-proofs")
PLACEMENT_EVIDENCE_DIR = os.path.join(PAYMENT_PROOFS_DIR, "placement-evidence")
MAX_PAYMENT_PROOF_BYTES = 5 * 1024 * 1024
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SESSION_SIGNING_KEY = os.getenv("SESSION_SIGNING_KEY", "")
TELEGRAM_LOGIN_MAX_AGE_SECONDS = 300
SESSION_TTL_SECONDS = 12 * 60 * 60
bearer = HTTPBearer(auto_error=False)


class TelegramLoginPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    is_friend: bool = False
    has_launched_ads: bool = False
    is_owner: bool = False
    is_developer: bool = False
    phone_verified: bool = False
    id: UUID
    telegram_user_id: int
    role: str
    first_name: str
    last_name: str | None
    username: str | None


class TariffResponse(BaseModel):
    id: UUID
    name: str
    duration_seconds: int
    price_kopecks: int
    currency: str
    admin_only: bool = False
    stars_price_xtr: int | None = None


class TariffAdminResponse(TariffResponse):
    is_active: bool


class TariffSettingsUpdate(BaseModel):
    """Редактируем тариф, не меняя уже зафиксированные цены заказов."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    duration_seconds: int = Field(ge=3600, le=31536000)
    price_kopecks: int = Field(ge=0, le=100000000)
    stars_price_xtr: int | None = Field(ge=0, le=1000000)
    is_active: bool

    @field_validator("price_kopecks")
    @classmethod
    def positive_sbp_minimum(cls, value):
        if 0 < value < 100:
            raise ValueError("price_must_be_zero_or_at_least_one_ruble")
        return value

    @field_validator("stars_price_xtr")
    @classmethod
    def zero_stars(cls, value):
        return None if value == 0 else value

    @field_validator("is_active")
    @classmethod
    def consistent_free_price(cls, value, info):
        if info.data.get("price_kopecks") == 0 and info.data.get("stars_price_xtr"):
            raise ValueError("free_tariff_requires_zero_or_empty_stars")
        return value

    @field_validator("name")
    @classmethod
    def normalize_tariff_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("tariff_name_required")
        return value


class TariffCreate(TariffSettingsUpdate):
    """UUID сохраняется при повторе запроса, чтобы не создавать дубликаты."""
    request_id: UUID
    admin_only: bool = False

    @model_validator(mode="after")
    def private_tariff_is_free(self):
        if self.admin_only and (self.price_kopecks != 0 or self.stars_price_xtr is not None):
            raise ValueError("admin_tariff_must_be_free")
        return self


class ChannelCreate(BaseModel):
    telegram_url: str = Field(min_length=12, max_length=255)
    title: str = Field(min_length=1, max_length=255)


class ChannelResponse(BaseModel):
    id: UUID
    telegram_url: str
    title: str


class CampaignCreate(BaseModel):
    channel_id: UUID
    tariff_id: UUID
    topic: Topic | None = None
    requested_start_at: datetime | None = None

    @field_validator("requested_start_at")
    @classmethod
    def validate_requested_start(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Укажите часовой пояс")
            if value <= datetime.now(timezone.utc):
                raise ValueError("Выберите время в будущем")
        return value


class OrderSubmission(BaseModel):
    """Единая отправка: свой канал либо новый публичный канал, выбранный тариф и итог."""
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    channel_id: UUID | None = None
    channel: ChannelCreate | None = None
    tariff_id: UUID
    expected_price_kopecks: int = Field(ge=0)
    expected_duration_seconds: int = Field(gt=0)
    promo_code: str | None = Field(default=None, max_length=48)
    quote_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    topic: Topic | None = None
    requested_start_at: datetime | None = None

    @model_validator(mode="after")
    def check_channel(self):
        if (self.channel_id is None) == (self.channel is None):
            raise ValueError("Выберите канал или укажите новый")
        if self.requested_start_at is not None and self.requested_start_at.utcoffset() is None:
            raise ValueError("Укажите часовой пояс")
        return self


class CampaignResponse(BaseModel):
    topic: Topic | None = None
    topic_label: str | None = None
    id: UUID
    channel_id: UUID
    tariff_id: UUID
    tariff_name_snapshot: str
    duration_seconds_snapshot: int
    price_kopecks_snapshot: int
    currency_snapshot: str
    moderation_status: str
    ai_rejected: bool = False
    ai_description: str | None = None
    ai_checked_at: datetime | None = None
    moderation_check: Literal['processing', 'review', 'retry'] | None = None
    moderation_reason: str | None = None
    moderation_attempted_at: datetime | None = None
    moderation_retry_at: datetime | None = None
    payment_status: str
    placement_status: str
    requested_start_at: datetime | None
    auto_start_not_before: datetime | None = None
    auto_schedule_tag_pending: bool = False
    auto_schedule_paused: bool = False
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    moderator_comment: str | None
    payment_review_comment: str | None = None
    channel_title: str | None = None
    channel_url: str | None = None
    archived_at: datetime | None = None
    archive_pending_actions: list[str] = Field(default_factory=list)
    base_price_kopecks_snapshot: int | None = None
    stars_price_snapshot: int | None = None
    growth_offer_name: str | None = None
    discount_percent_snapshot: int | None = None


class OperatorCampaignResponse(CampaignResponse):
    proxy_node_id: UUID | None = None
    advertiser_user_id: UUID | None = None
    advertiser_first_name: str | None = None
    advertiser_last_name: str | None = None
    advertiser_username: str | None = None
    advertiser_telegram_user_id: int | None = None


class PaymentReviewRequest(BaseModel):
    decision: str = Field(pattern="^(verified|rejected)$")
    comment: str | None = Field(default=None, max_length=2000)


class PlacementScheduleRequest(BaseModel):
    proxy_node_id: UUID
    start_at: datetime


class SlotHoldRequest(BaseModel):
    proxy_node_id: UUID
    start_at: datetime


class PaymentProofResponse(BaseModel):
    id: UUID
    original_file_name: str
    content_type: str
    size_bytes: int
    submitted_at: datetime


class ModerationRequest(BaseModel):
    reconsider_ai: bool = False
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    comment: str | None = Field(default=None, max_length=2000)


class ModeratorDecisionRequest(ModerationRequest):
    operator_decision_id: UUID | None = None


class PlacementCompletionRequest(BaseModel):
    """Явное ручное подтверждение удаления тега; не результат Telegram API."""
    tag_removed_confirmed: Literal[True]
    verification_method: Literal["separate_unsubscribed_client"]


class PlacementObservationRequest(BaseModel):
    result: Literal["visible", "not_visible"]
    method: Literal["separate_unsubscribed_client"]
    observed_at: datetime
    note: str = Field(min_length=12, max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_observation_note(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 12:
            raise ValueError("observation_note_too_short")
        return value


def _required_secret(value: str, name: str) -> bytes:
    if not value or value.startswith("REPLACE_WITH"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name.lower()}_not_configured",
        )
    key = value.encode()
    if name == "SESSION_SIGNING_KEY" and len(key) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="session_signing_key_too_short",
        )
    return key


def _verify_telegram_login(payload: TelegramLoginPayload) -> None:
    bot_token = _required_secret(TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN")
    now = int(time.time())
    if payload.auth_date > now + 60 or now - payload.auth_date > TELEGRAM_LOGIN_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="telegram_login_expired")

    received_hash = payload.hash.lower()
    raw_fields = payload.model_dump(exclude={"hash"}, exclude_none=True)
    fields = {
        key: ("true" if value else "false") if isinstance(value, bool) else str(value)
        for key, value in raw_fields.items()
    }
    data_check_string = "\n".join(
        f"{key}={fields[key]}" for key in sorted(fields)
    )
    secret_key = hashlib.sha256(bot_token).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="telegram_login_invalid_signature")


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode_b64url(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _create_session_token(user_id: UUID, session_id: UUID) -> str:
    key = _required_secret(SESSION_SIGNING_KEY, "SESSION_SIGNING_KEY")
    payload = json.dumps(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "exp": int(time.time()) + SESSION_TTL_SECONDS,
        },
        separators=(",", ":"),
    ).encode()
    payload_part = _b64url(payload)
    signature = hmac.new(key, payload_part.encode(), hashlib.sha256).digest()
    return f"{payload_part}.{_b64url(signature)}"


def _decode_session_token(token: str) -> tuple[UUID, UUID, int]:
    key = _required_secret(SESSION_SIGNING_KEY, "SESSION_SIGNING_KEY")
    try:
        payload_part, signature_part = token.split(".", 1)
        signature = _decode_b64url(signature_part)
        expected = hmac.new(key, payload_part.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(_decode_b64url(payload_part))
        user_id = UUID(payload["sub"])
        session_id = UUID(payload["sid"])
        expires_at = int(payload["exp"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
        raise HTTPException(status_code=401, detail="invalid_access_token") from None
    if expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="access_token_expired")
    return user_id, session_id, expires_at


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="authentication_required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id, session_id, _ = _decode_session_token(credentials.credentials)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            user = connection.execute(
                """
                SELECT u.id, u.telegram_user_id, u.role, u.is_owner, u.is_developer, u.is_friend, u.has_launched_ads, u.first_name, u.last_name,
                       u.username, u.blocked_at, s.id AS session_id, s.login_surface,
                       EXISTS(SELECT 1 FROM verified_profiles v WHERE v.user_id=u.id AND v.phone_verified_at IS NOT NULL AND v.telegram_environment=%s) AS phone_verified
                FROM users AS u
                JOIN user_sessions AS s ON s.user_id = u.id
                WHERE u.id = %s AND s.id = %s
                  AND s.revoked_at IS NULL AND s.expires_at > now()
                """,
                (environment_name(), user_id, session_id),
            ).fetchone()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="session_revoked_or_expired")
    if user['blocked_at'] is not None:
        raise HTTPException(status_code=401, detail='user_blocked')
    # Разработчик получает весь административный интерфейс и API. В БД
    # разработчик обычно хранится как admin + is_developer, но поддерживаем
    # и отдельную роль developer, чтобы проверки по role не урезали права.
    if user['role'] == 'developer':
        user['is_developer'] = True
    if user.get('is_developer') or user.get('is_owner'):
        user['role'] = 'admin'
    return user


def require_operator(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] not in {"operator", "moderator", "admin", "developer"}:
        raise HTTPException(status_code=403, detail="operator_role_required")
    return user


def require_moderator(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] not in {"moderator", "operator", "admin"}:
        raise HTTPException(403, "moderator_role_required")
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] != "admin" and not user.get("is_developer") and not user.get("is_owner"):
        raise HTTPException(status_code=403, detail="admin_role_required")
    return user


def require_owner(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not (user.get('is_developer') or user.get('is_owner')):
        raise HTTPException(status_code=403, detail='owner_role_required')
    return user


def _campaign_response(row: dict[str, Any]) -> CampaignResponse:
    if row.get("currency_snapshot") is not None:
        row["currency_snapshot"] = row["currency_snapshot"].strip()
    if row.get("archived_at"):
        row["archive_pending_actions"] = [reason for reason, pending in (
            ("placement", row.get("placement_status") == "active"),
            ("proof", row.get("payment_status") == "proof_submitted"),
            ("payment", row.get("has_pending_payment", False)),
        ) if pending]
    row["topic_label"] = topic_label(row.get("topic"))
    return CampaignResponse(**row)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> dict[str, str]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"status": "ready", "database": "ok"}


@app.post("/api/v1/auth/telegram", response_model=SessionResponse, tags=["authentication"])
def telegram_login(payload: TelegramLoginPayload) -> SessionResponse:
    _verify_telegram_login(payload)
    return _start_telegram_session(payload)


class MiniAppLoginPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    init_data: str = Field(min_length=1, max_length=16384)


@app.post('/api/v1/auth/telegram/miniapp', response_model=SessionResponse, tags=['authentication'])
def miniapp_login(payload: MiniAppLoginPayload, response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    if os.getenv('PUSHOK_MINIAPP_ENABLED', '0') != '1':
        raise HTTPException(409, 'miniapp_disabled')
    bot_token = _required_secret(TELEGRAM_BOT_TOKEN, 'TELEGRAM_BOT_TOKEN').decode()
    verified = validate_init_data(payload.init_data, bot_token)
    return _start_telegram_session(TelegramLoginPayload(**verified), surface="telegram")


def _start_telegram_session(payload: TelegramLoginPayload, *, surface="web") -> SessionResponse:
    _required_secret(SESSION_SIGNING_KEY, 'SESSION_SIGNING_KEY')
    session_id = uuid4()
    expires_at = datetime.fromtimestamp(
        int(time.time()) + SESSION_TTL_SECONDS, tz=timezone.utc
    )
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM telegram_login_replays WHERE consumed_at < now() - interval '1 day'"
                )
                inserted = cursor.execute(
                    """
                    INSERT INTO telegram_login_replays (telegram_hash, auth_date)
                    VALUES (%s, to_timestamp(%s))
                    ON CONFLICT (telegram_hash) DO NOTHING
                    RETURNING telegram_hash
                    """,
                    (payload.hash.lower(), payload.auth_date),
                ).fetchone()
                if inserted is None:
                    raise HTTPException(status_code=401, detail="telegram_login_replayed")

                user = cursor.execute(
                    """
                    INSERT INTO users (telegram_user_id, first_name, last_name, username)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (telegram_user_id) DO UPDATE SET
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        username = EXCLUDED.username,
                        updated_at = now()
                    RETURNING id,blocked_at
                    """,
                    (payload.id, payload.first_name, payload.last_name, payload.username),
                ).fetchone()
                if user['blocked_at'] is not None:
                    raise HTTPException(403, 'user_blocked')
                cursor.execute(
                    """
                    INSERT INTO user_sessions (id, user_id, expires_at, login_surface)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (session_id, user["id"], expires_at, surface),
                )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc

    return SessionResponse(
        access_token=_create_session_token(user["id"], session_id),
        expires_in=SESSION_TTL_SECONDS,
    )


@app.get("/api/v1/me", response_model=UserResponse, tags=["account"])
def get_me(user: dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    return UserResponse(**(user if can_view_username(user) else hide_username(dict(user))))


@app.post("/api/v1/auth/logout", status_code=204, tags=["authentication"])
def logout(user: dict[str, Any] = Depends(get_current_user)) -> None:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at = now() WHERE id = %s AND user_id = %s",
                (user["session_id"], user["id"]),
            )
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc


def _available_tariffs(*, include_admin: bool = False) -> list[TariffResponse]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            rows = connection.execute(
                """
                SELECT id, name, duration_seconds, price_kopecks, currency, admin_only, stars_price_xtr
                FROM tariffs
                WHERE is_active = TRUE AND archived_at IS NULL AND (NOT admin_only OR %s)
                ORDER BY admin_only, duration_seconds, price_kopecks, id
                """, (include_admin,),
            ).fetchall()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [
        TariffResponse(
            id=row[0], name=row[1], duration_seconds=row[2],
            price_kopecks=row[3], currency=row[4].strip(), admin_only=row[5], stars_price_xtr=row[6],
        )
        for row in rows
    ]


@app.get("/api/v1/tariffs", response_model=list[TariffResponse], tags=["tariffs"])
def list_tariffs() -> list[TariffResponse]:
    """Публичный каталог никогда не включает служебные тарифы."""
    return _available_tariffs()


@app.get("/api/v1/me/tariffs", response_model=list[TariffResponse], tags=["tariffs"])
def list_available_tariffs(response: Response, user=Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return _available_tariffs(include_admin=user["role"] == "admin")


@app.post("/api/v1/channels", response_model=ChannelResponse, status_code=201, tags=["channels"])
def create_channel(
    payload: ChannelCreate,
    user: dict[str, Any] = Depends(get_current_user),
) -> ChannelResponse:
    parsed = urlparse(payload.telegram_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"t.me", "telegram.me"}
        or not parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=422, detail="invalid_telegram_channel_url")
    normalized_title = payload.title.strip()
    if not normalized_title:
        raise HTTPException(status_code=422, detail="channel_title_required")
    from . import public_channels
    checked = public_channels.lookup(payload.telegram_url, fresh=True)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                INSERT INTO sponsor_channels (owner_user_id, telegram_url, title, moderation_chat_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, telegram_url, title
                """,
                (user["id"], checked['telegram_url'], normalized_title, checked['telegram_channel_id']),
            ).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="channel_already_added") from exc
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return ChannelResponse(**row)


@app.post("/api/v1/campaigns", response_model=CampaignResponse, status_code=201, tags=["campaigns"])
def create_campaign(
    payload: CampaignCreate,
    user: dict[str, Any] = Depends(get_current_user),
) -> CampaignResponse:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            from .community import require_verified
            require_verified(connection,user["id"])
            row = connection.execute(
                """
                INSERT INTO campaigns (
                    advertiser_user_id, channel_id, tariff_id,
                    tariff_name_snapshot, duration_seconds_snapshot,
                    price_kopecks_snapshot, currency_snapshot, requested_start_at, purchase_surface, topic
                )
                SELECT %s, c.id, t.id, t.name, t.duration_seconds,
                       t.price_kopecks, t.currency, %s, %s, %s
                FROM sponsor_channels AS c
                JOIN tariffs AS t ON t.id = %s AND t.is_active = TRUE AND t.archived_at IS NULL
                  AND (NOT t.admin_only OR %s)
                WHERE c.id = %s AND c.owner_user_id = %s AND c.archived_at IS NULL
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment, topic
                """,
                (user["id"], payload.requested_start_at, user.get("login_surface", "web"), payload.topic, payload.tariff_id,
                 user["role"] == "admin", payload.channel_id, user["id"]),
            ).fetchone()
    except InsufficientPrivilege as exc:
        if exc.diag.message_primary == "admin_tariff_required":
            raise HTTPException(status_code=403, detail="admin_tariff_required") from None
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="channel_or_active_tariff_not_found")
    return _campaign_response(row)


@app.post("/api/v1/orders", response_model=CampaignResponse, status_code=201, tags=["campaigns"])
def submit_order(payload: OrderSubmission, user=Depends(get_current_user)):
    from .order_submission import create_order
    return _campaign_response(create_order(DATABASE_URL, payload, user))


@app.get("/api/v1/campaigns", response_model=list[CampaignResponse], tags=["campaigns"])
def list_campaigns(
    user: dict[str, Any] = Depends(get_current_user),
) -> list[CampaignResponse]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT sc.title AS channel_title, sc.telegram_url AS channel_url,
                       c.id, c.channel_id, c.tariff_id, c.tariff_name_snapshot,
                       c.duration_seconds_snapshot, c.price_kopecks_snapshot, c.base_price_kopecks_snapshot, c.stars_price_snapshot,
                       c.growth_offer_name, c.discount_percent_snapshot, c.topic,
                       c.currency_snapshot, c.moderation_status, c.payment_status,
                       c.placement_status, c.requested_start_at, c.scheduled_start_at,
                       c.scheduled_end_at, c.auto_start_not_before, c.auto_schedule_tag_pending, c.auto_schedule_paused, c.moderator_comment,
                       latest_proof.review_comment AS payment_review_comment,
                       review.ai_description, review.ai_checked_at, review.ai_rejected,
                       CASE WHEN ac.lease_until>now() THEN 'processing' ELSE ac.decision END AS moderation_check,
                       NULLIF(ac.reason, '') AS moderation_reason, ac.attempted_at AS moderation_attempted_at,
                       CASE WHEN ac.lease_until>now() THEN NULL ELSE ac.retry_at END AS moderation_retry_at
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                JOIN campaign_agent_reviews review ON review.campaign_id=c.id
                LEFT JOIN ai_moderation_checks ac ON ac.campaign_id = c.id AND ac.snapshot = c.updated_at
                    AND c.moderation_status = 'pending' AND c.archived_at IS NULL
                    AND (ac.lease_until>now() OR ac.decision = 'review' OR (ac.decision = 'retry' AND ac.reason <> ''))
                LEFT JOIN LATERAL (
                    SELECT p.review_comment
                    FROM payment_proofs AS p
                    WHERE p.campaign_id = c.id AND p.reviewed_at IS NOT NULL
                    ORDER BY p.submitted_at DESC
                    LIMIT 1
                ) AS latest_proof ON TRUE
                WHERE c.advertiser_user_id = %s AND c.archived_at IS NULL
                ORDER BY c.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [_campaign_response(row) for row in rows]


@app.post(
    "/api/v1/campaigns/{campaign_id}/submit",
    response_model=CampaignResponse,
    tags=["campaigns"],
)
def submit_campaign(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> CampaignResponse:
    from . import public_channels
    snapshot = public_channels.owned_snapshot(DATABASE_URL, user['id'], campaign_id=campaign_id)
    checked = public_channels.verify_snapshot(snapshot)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            channel = connection.execute(
                """
                SELECT ch.id, ch.telegram_url, ch.telegram_channel_id FROM sponsor_channels AS ch
                JOIN campaigns AS c ON c.channel_id = ch.id
                WHERE c.id = %s AND c.advertiser_user_id = %s AND c.archived_at IS NULL AND ch.archived_at IS NULL
                FOR UPDATE OF ch
                """, (campaign_id, user["id"]),
            ).fetchone()
            if channel is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            public_channels.unchanged(channel, snapshot)
            connection.execute('UPDATE sponsor_channels SET moderation_chat_id=%s WHERE id=%s',
                               (checked['telegram_channel_id'], channel['id']))
            row = connection.execute(
                """
                UPDATE campaigns
                SET moderation_status = 'pending', updated_at = now()
                WHERE id = %s AND advertiser_user_id = %s AND moderation_status IN ('draft', 'changes_requested')
                  AND (payment_status = 'not_required' OR (moderation_status='changes_requested' AND payment_status='verified'))
                  AND placement_status = 'not_scheduled'
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (campaign_id, user["id"]),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 FROM campaigns WHERE id = %s AND advertiser_user_id = %s",
                    (campaign_id, user["id"]),
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="campaign_not_found")
                raise HTTPException(status_code=409, detail="campaign_not_editable")
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, campaign_id, event_type)
                VALUES (%s, %s, 'campaign_submitted')
                """,
                (user["id"], campaign_id),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


@app.get("/api/v1/moderation/queue", tags=["moderation"])
def moderator_queue(response: Response, page: int = Query(0, ge=0, le=100000), viewer=Depends(require_moderator)):
    response.headers["Cache-Control"] = "private, no-store"
    with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as conn:
        rows = conn.execute("""SELECT id,title,telegram_url,reason,description,moderation_status,operator_decision_id
            FROM (
                SELECT id,advertiser_user_id,owner_user_id,title,telegram_url,reason,description,attempted_at,
                       'pending' AS moderation_status,NULL::uuid AS operator_decision_id FROM moderator_review_queue
                UNION ALL
                SELECT id,advertiser_user_id,owner_user_id,title,telegram_url,reason,'' AS description,attempted_at,
                       moderation_status,operator_decision_id FROM moderation_operator_reviews WHERE %s
            ) queue WHERE advertiser_user_id<>%s AND owner_user_id<>%s
            ORDER BY (operator_decision_id IS NOT NULL),attempted_at DESC,id LIMIT 21 OFFSET %s""",
            (viewer['role'] in ('moderator','admin'), viewer['id'], viewer['id'], page * 20)).fetchall()
    return {"items": rows[:20], "more": len(rows) > 20}


@app.post("/api/v1/moderation/queue/{campaign_id}", tags=["moderation"])
def decide_disputed_campaign(campaign_id: UUID, payload: ModeratorDecisionRequest, viewer=Depends(require_moderator)):
    if payload.reconsider_ai:
        raise HTTPException(409, "campaign_not_pending")
    result = _moderate_campaign(campaign_id, payload, viewer, disputed_only=True,
                                operator_decision_id=payload.operator_decision_id)
    return {"moderation_status": result.moderation_status}


@app.post(
    "/api/v1/operator/campaigns/{campaign_id}/moderation",
    response_model=CampaignResponse,
    tags=["operator"],
)
def moderate_campaign(
    campaign_id: UUID,
    payload: ModerationRequest,
    operator: dict[str, Any] = Depends(require_operator),
) -> CampaignResponse:
    return _moderate_campaign(campaign_id, payload, operator)


def _moderate_campaign(campaign_id, payload, operator, *, disputed_only=False, operator_decision_id=None):
    if (payload.decision != "approved" or operator_decision_id) and not (payload.comment and payload.comment.strip()):
        raise HTTPException(status_code=422, detail="moderation_comment_required")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            allowed_roles = ('admin','operator','moderator') if disputed_only else ('admin','operator')
            actor = connection.execute("SELECT role,blocked_at FROM users WHERE id=%s FOR SHARE",
                                       (operator['id'],)).fetchone()
            if not actor or actor['blocked_at'] or actor['role'] not in allowed_roles:
                raise HTTPException(403, "moderator_role_required" if disputed_only else "operator_role_required")
            previous_status = "rejected" if payload.reconsider_ai else "pending"
            if disputed_only:
                connection.execute("SELECT id FROM campaigns WHERE id=%s FOR UPDATE", (campaign_id,))
                if operator_decision_id:
                    if actor['role'] not in ('moderator','admin'):
                        raise HTTPException(403, "moderator_role_required")
                    eligible = connection.execute("""SELECT moderation_status FROM moderation_operator_reviews
                        WHERE id=%s AND advertiser_user_id<>%s AND owner_user_id<>%s AND operator_decision_id=%s""",
                        (campaign_id, operator['id'], operator['id'], operator_decision_id)).fetchone()
                    if not eligible or eligible['moderation_status']==payload.decision:
                        raise HTTPException(409, "operator_decision_changed")
                    previous_status = eligible['moderation_status']
                    connection.execute("""UPDATE bot_advertiser_notice_outbox
                        SET abandoned_at=clock_timestamp(), claim_token=NULL, lease_until=NULL
                        WHERE campaign_id=%s AND delivered_at IS NULL AND abandoned_at IS NULL""", (campaign_id,))
                else:
                    eligible = connection.execute("""SELECT id FROM moderator_review_queue WHERE id=%s
                        AND advertiser_user_id<>%s AND owner_user_id<>%s""",
                        (campaign_id, operator['id'], operator['id'])).fetchone()
                    if not eligible:
                        raise HTTPException(409, "campaign_not_pending")
            if payload.reconsider_ai:
                # Повторная проверка после блок…22074 tokens truncated…           blocked = connection.execute(
                """
                SELECT 1 FROM campaigns WHERE channel_id = %s AND (
                    moderation_status NOT IN ('draft', 'changes_requested')
                    OR payment_status <> 'not_required'
                    OR placement_status <> 'not_scheduled')
                LIMIT 1
                """, (channel["id"],),
            ).fetchone()
            if blocked:
                raise HTTPException(status_code=409, detail="channel_in_review_or_approved")
            row = connection.execute(
                """
                UPDATE sponsor_channels SET title = %s, telegram_url = %s,
                    telegram_channel_id = NULL
                WHERE id = %s RETURNING id, telegram_url, title
                """, (title, payload.telegram_url.rstrip("/"), channel["id"]),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, campaign_id, event_type)
                VALUES (%s, %s, 'campaign_channel_corrected')
                """, (user["id"], campaign_id),
            )
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="channel_already_added") from exc
    except HTTPException:
        raise
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return ChannelResponse(**row)




@app.get("/api/v1/campaigns/{campaign_id}/payment-instructions", tags=["payments"])
def get_payment_instructions(
    campaign_id: UUID,
    response: Response,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """
                SELECT id, price_kopecks_snapshot, currency_snapshot
                FROM campaigns
                WHERE id = %s AND advertiser_user_id = %s
                  AND moderation_status = 'approved'
                  AND payment_status IN ('awaiting_payment', 'rejected', 'proof_submitted', 'verified')
                """, (campaign_id, user["id"]),
            ).fetchone()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    try:
        raw = os.getenv("PAYMENT_INSTRUCTIONS_B64", "")
        if len(raw) > 22000:
            raise ValueError("too long")
        instructions = b64decode(raw, validate=True).decode("utf-8").strip()
        if not instructions or len(instructions) > 4000:
            raise ValueError("invalid instructions")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=503, detail="payment_instructions_not_configured") from None
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "campaign_id": campaign["id"],
        "amount_kopecks": campaign["price_kopecks_snapshot"],
        "currency": campaign["currency_snapshot"].strip(),
        "reference": str(campaign["id"]),
        "instructions": instructions,
        "verification": "manual",
    }




def _sbp_action(action, campaign_id, user, response):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return action(DATABASE_URL, campaign_id, user['id'])
    except HTTPException:
        raise
    except SBPProviderError as exc:
        if str(exc) == 'sbp_test_mode_mismatch':
            raise HTTPException(409, 'sbp_test_mode_mismatch') from exc
        raise HTTPException(503, 'sbp_provider_unavailable') from exc
    except (SBPReconcileError, SBPVerificationError) as exc:
        raise HTTPException(409, 'sbp_verification_requires_review') from exc
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(503, 'database_unavailable') from exc


class SBPAdminRetryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    note: str = Field(min_length=12, max_length=300)


def _sbp_admin_action(action, response):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return action()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(503, 'database_unavailable') from exc


@app.get('/api/v1/admin/payments/sbp/attempts', tags=['admin'])
def admin_sbp_attempts(response: Response, offset: int = Query(0, ge=0, le=100000),
                       limit: int = Query(50, ge=1, le=100), admin=Depends(require_admin)):
    return _sbp_admin_action(lambda: sbp_admin.list_attempts(DATABASE_URL, offset=offset, limit=limit), response)


@app.post('/api/v1/admin/payments/sbp/attempts/{attempt_id}/retry', tags=['admin'])
def admin_sbp_retry(attempt_id: UUID, body: SBPAdminRetryRequest,
                    response: Response, admin=Depends(require_admin)):
    return _sbp_admin_action(lambda: sbp_admin.request_recheck(
        DATABASE_URL, attempt_id, admin['id'], body.revision, body.note), response)


def _stars_web_action(action, campaign_id, user, response):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        return action(DATABASE_URL, campaign_id, user['id'])
    except stars_checkout.StarsLinkError:
        raise HTTPException(503, 'stars_link_unavailable') from None
    except psycopg.Error:
        raise HTTPException(503, 'database_unavailable') from None


@app.get('/api/v1/campaigns/{campaign_id}/stars', tags=['payments'])
def read_stars_checkout(campaign_id: UUID, response: Response, user=Depends(get_current_user)):
    return _stars_web_action(stars_checkout.read_checkout, campaign_id, user, response)


@app.post('/api/v1/campaigns/{campaign_id}/stars/checkout', tags=['payments'])
def create_stars_checkout(campaign_id: UUID, response: Response, user=Depends(get_current_user)):
    return _stars_web_action(stars_checkout.checkout, campaign_id, user, response)


@app.get('/api/v1/campaigns/{campaign_id}/sbp', tags=['payments'])
def read_sbp_checkout(campaign_id: UUID, response: Response,
                      user: dict[str, Any] = Depends(get_current_user)):
    return _sbp_action(sbp_checkout.read_checkout, campaign_id, user, response)


@app.post('/api/v1/campaigns/{campaign_id}/sbp/checkout', tags=['payments'])
def create_sbp_checkout(campaign_id: UUID, response: Response,
                        user: dict[str, Any] = Depends(get_current_user)):
    return _sbp_action(sbp_checkout.create_checkout, campaign_id, user, response)


@app.post('/api/v1/campaigns/{campaign_id}/sbp/reconcile', tags=['payments'])
def verify_sbp_checkout(campaign_id: UUID, response: Response,
                        user: dict[str, Any] = Depends(get_current_user)):
    return _sbp_action(sbp_checkout.verify_checkout, campaign_id, user, response)


@app.get("/api/v1/availability", tags=["calendar"])
def get_availability(
    tariff_id: UUID,
    date_from: Date,
    days: int = 14,
    timezone_name: str = "Europe/Moscow",
    start_time: Time = Time(12, 0),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if days < 1 or days > 31 or start_time.tzinfo is not None:
        raise HTTPException(status_code=422, detail="invalid_calendar_range")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="invalid_calendar_timezone") from None
    today = datetime.now(zone).date()
    if date_from < today or date_from + timedelta(days=days - 1) > today + timedelta(days=90):
        raise HTTPException(status_code=422, detail="invalid_calendar_range")
    now = datetime.now(timezone.utc)
    starts = [datetime.combine(date_from + timedelta(days=i), start_time, tzinfo=zone) for i in range(days)]
    # В дни перехода часов не предлагаем несуществующее локальное время.
    valid = [dt for dt in starts if slot_status(dt, now, 1) == "available"]
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            tariff = connection.execute(
                """SELECT duration_seconds FROM tariffs WHERE id = %s AND is_active = TRUE AND archived_at IS NULL
                   AND (NOT admin_only OR %s)""",
                (tariff_id, user["role"] == "admin"),
            ).fetchone()
            if tariff is None:
                raise HTTPException(status_code=404, detail="active_tariff_not_found")
            active_nodes = connection.execute("SELECT count(*) AS total FROM proxy_nodes WHERE status='active' AND archived_at IS NULL").fetchone()["total"]
            duration = timedelta(seconds=tariff["duration_seconds"])
            ends = [dt.astimezone(timezone.utc) + duration for dt in valid]
            rows = connection.execute(
                """
                SELECT w.starts_at, count(n.id) AS available_nodes
                FROM unnest(%s::timestamptz[], %s::timestamptz[]) AS w(starts_at, ends_at)
                LEFT JOIN proxy_nodes AS n ON n.status = 'active' AND n.archived_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM campaigns AS c
                    WHERE c.proxy_node_id = n.id
                      AND c.placement_status IN ('scheduled', 'active')
                      AND tstzrange(c.scheduled_start_at, c.scheduled_end_at, '[)')
                          && tstzrange(w.starts_at, w.ends_at, '[)')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM slot_holds AS h
                    WHERE h.proxy_node_id = n.id AND h.expires_at > now()
                      AND tstzrange(h.starts_at, h.ends_at, '[)')
                          && tstzrange(w.starts_at, w.ends_at, '[)')
                  )
                GROUP BY w.starts_at ORDER BY w.starts_at
                """, (valid, ends),
            ).fetchall() if valid else []
    except HTTPException:
        raise
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    counts = {row["starts_at"]: row["available_nodes"] for row in rows}
    return {
        "tariff_id": tariff_id, "timezone": timezone_name,
        "duration_seconds": tariff["duration_seconds"], "reservation": True,
        "hold_ttl_seconds": 900,
        "days": [
            {"date": dt.date().isoformat(), "start_at": dt.isoformat(),
             "end_at": (dt.astimezone(timezone.utc) + duration).isoformat(),
             "available_nodes": counts.get(dt, 0) if dt in valid else 0,
             "status": ("unconfigured" if not active_nodes and dt in valid else slot_status(dt, now, int(counts.get(dt, 0)) if dt in valid else 0))}
            for dt in starts
        ],
    }


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
@app.get("/update/status.json", include_in_schema=False)
def public_update_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    from . import control
    status = control.read("status.json", {})
    if not isinstance(status, dict):
        status = {}
    state = status.get("state", "idle")
    data = {"state": state if state in ("idle", "running", "succeeded", "failed") else "idle"}
    stage = status.get("stage")
    if state == "running" and stage in ("preflight", "backup", "source", "build", "migration", "restart", "health", "proxy"):
        data["stage"] = stage
    return data


app.mount("/update", StaticFiles(directory=WEB_DIR / "update"), name="update")
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get('/api/v1/miniapp/config', tags=['configuration'])
def miniapp_config(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {'enabled': os.getenv('PUSHOK_MINIAPP_ENABLED', '0') == '1',
            'loading_title': _loading_title()}


DEFAULT_LOADING_TITLE = 'Пушок готовит кабинет…'


def _loading_title():
    value = control.read('miniapp-loading-title.json', DEFAULT_LOADING_TITLE)
    return value if isinstance(value, str) and 1 <= len(value.strip()) <= 100 else DEFAULT_LOADING_TITLE


class LoadingTitleSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    loading_title: str = Field(min_length=1, max_length=100)

    @field_validator('loading_title')
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Укажите текст в одну строку')
        return value


@app.get('/api/v1/admin/miniapp/loading', tags=['admin'])
def read_loading_title(admin=Depends(require_admin)):
    return {'loading_title': _loading_title()}


@app.put('/api/v1/admin/miniapp/loading', tags=['admin'])
def save_loading_title(payload: LoadingTitleSettings, admin=Depends(require_admin)):
    try:
        control.write('miniapp-loading-title.json', payload.loading_title)
    except OSError as exc:
        raise HTTPException(503, 'loading_settings_unavailable') from exc
    return {'loading_title': payload.loading_title}



@app.get('/miniapp', response_class=HTMLResponse, include_in_schema=False)
@app.get('/miniapp/', response_class=HTMLResponse, include_in_schema=False)
def miniapp_client():
    html = (WEB_DIR / 'index.html').read_text()
    html = html.replace('initial-scale=1', 'initial-scale=1, viewport-fit=cover')
    html = html.replace('<body>', '<body data-miniapp="true" class="miniapp-mode miniapp-loading">')
    # Вход Mini App не должен зависеть от версии кеша основного скрипта.
    html = html.replace('<script src="/assets/app.js',
                        '<script src="/assets/miniapp.js?v=1.126" defer></script>\n  <script src="/assets/app.js', 1)
    return HTMLResponse(html, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@app.get("/", include_in_schema=False)
def cabinet() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


# v0.022 — Регистрация MTProxyBot и подготовка без реального изменения прокси.
class SponsorRegistrationRequest(BaseModel):
    engine: str = Field(pattern="^(telemt|official_mtproxy|official_docker)$")
    ad_tag: str = Field(min_length=32, max_length=32)

    @field_validator("ad_tag")
    @classmethod
    def validate_ad_tag(cls, value: str) -> str:
        try:
            return normalize_ad_tag(value)
        except ValueError as exc:
            raise ValueError("Ожидается настоящий 32-символьный ad_tag из @MTProxyBot") from exc


class SponsorRegistrationResponse(BaseModel):
    proxy_node_id: UUID
    engine: str
    tag_ending: str
    changes_applied: bool = False


class SponsorConfigPreview(BaseModel):
    engine: str
    config_fragment: str
    changes_applied: bool = False
    warning: str = "Это только образец: MTProxy не изменён, Set promotion выполняется вручную."


@app.put(
    "/api/v1/admin/proxy-nodes/{node_id}/sponsor-registration",
    response_model=SponsorRegistrationResponse, tags=["admin"],
)
def save_proxy_sponsor_registration(
    node_id: UUID,
    payload: SponsorRegistrationRequest,
    admin: dict[str, Any] = Depends(require_admin),
) -> SponsorRegistrationResponse:
    """Записать тег, полученный вручную через @MTProxyBot, без применения."""
    try:
        with psycopg.connect(
            DATABASE_URL, connect_timeout=3, row_factory=dict_row
        ) as connection:
            node = connection.execute(
                "SELECT id, target_engine FROM proxy_nodes WHERE id = %s AND archived_at IS NULL FOR UPDATE",
                (node_id,),
            ).fetchone()
            if node is None:
                raise HTTPException(status_code=404, detail="proxy_node_not_found")
            if node.get("target_engine") not in (None, payload.engine):
                raise HTTPException(status_code=409, detail="sponsor_engine_mismatch")
            occupied = connection.execute(
                """
                SELECT 1 FROM campaigns
                WHERE proxy_node_id = %s AND placement_status IN ('scheduled', 'active')
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            if occupied:
                raise HTTPException(status_code=409, detail="proxy_node_has_placements")
            connection.execute(
                """
                INSERT INTO proxy_sponsor_registrations
                    (proxy_node_id, engine, ad_tag, registered_by_user_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (proxy_node_id) DO UPDATE
                SET engine = EXCLUDED.engine, ad_tag = EXCLUDED.ad_tag,
                    registered_by_user_id = EXCLUDED.registered_by_user_id,
                    registered_at = now()
                """,
                (node_id, payload.engine, payload.ad_tag, admin["id"]),
            )
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, event_type, details_json)
                VALUES (%s, 'sponsor_tag_registered',
                        jsonb_build_object('proxy_node_id', %s::text,
                                           'engine', %s::text))
                """,
                (admin["id"], str(node_id), payload.engine),
            )
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="sponsor_tag_already_registered") from exc
    except HTTPException:
        raise
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return SponsorRegistrationResponse(
        proxy_node_id=node_id, engine=payload.engine, tag_ending=payload.ad_tag[-4:],
    )


@app.get(
    "/api/v1/admin/proxy-nodes/{node_id}/sponsor-config-preview",
    response_model=SponsorConfigPreview, tags=["admin"],
)
def preview_proxy_sponsor_config(
    node_id: UUID,
    admin: dict[str, Any] = Depends(require_admin),
) -> SponsorConfigPreview:
    """Вернуть копируемый фрагмент только администратору; не менять файлы."""
    try:
        with psycopg.connect(
            DATABASE_URL, connect_timeout=3, row_factory=dict_row
        ) as connection:
            row = connection.execute(
                """
                SELECT engine, ad_tag FROM proxy_sponsor_registrations
                WHERE proxy_node_id = %s
                """,
                (node_id,),
            ).fetchone()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="sponsor_registration_not_found")
    return SponsorConfigPreview(
        engine=row["engine"],
        config_fragment=render_config_fragment(row["engine"], row["ad_tag"]),
    )


@app.get(
    "/api/v1/operator/campaigns/{campaign_id}/sponsor-readiness",
    tags=["operator"],
)
def sponsor_placement_readiness(
    campaign_id: UUID,
    operator: dict[str, Any] = Depends(require_operator),
) -> dict[str, Any]:
    """Проверка кампании перед ручным Set promotion, без записи в БД."""
    try:
        with psycopg.connect(
            DATABASE_URL, connect_timeout=3, row_factory=dict_row
        ) as connection:
            row = connection.execute(
                """
                SELECT c.id AS campaign_id, c.proxy_node_id,
                       c.moderation_status, c.payment_status, c.placement_status,
                       c.scheduled_start_at, c.scheduled_end_at,
                       sc.telegram_url, sc.telegram_channel_id,
                       n.host AS proxy_host, n.port AS proxy_port,
                       n.status AS node_status,
                       sr.engine, sr.ad_tag
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                LEFT JOIN proxy_nodes AS n ON n.id = c.proxy_node_id
                LEFT JOIN proxy_sponsor_registrations AS sr
                       ON sr.proxy_node_id = n.id
                WHERE c.id = %s
                """,
                (campaign_id,),
            ).fetchone()
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    return build_readiness(row)

# Управление настройками доступно только владельцу с действующей сессией.
from . import control

class PaymentSwitches(BaseModel):
    stars: bool
    sbp: bool

@app.get('/api/v1/admin/control', tags=['admin'])
def control_status(response: Response, admin=Depends(require_admin)):
    response.headers['Cache-Control'] = 'no-store'
    cfg = sbp_checkout.settings()
    installed = control.read('installed.json')
    auto_check = control.read('auto-check.json') or {}
    return {'version': app.version, 'stars': stars_invoices_enabled(),
            'sbp': cfg['enabled'], 'sbp_configured': cfg['configured'],
            'sbp_test_mode': cfg['test_mode'],
            'updater_installed': installed is not None,
            'auto_supported': (installed or {}).get('version') == 2,
            'auto_enabled': (control.read('auto-update.json') or {}).get('enabled') is True,
            'auto_checked_at': auto_check.get('checked_at'), 'auto_check_state': auto_check.get('state'),
            'update': control.read('status.json', {'state': 'idle'}),
            'requested_at': (control.read('request.json') or {}).get('at'),
            'requested': control.read('request.json') is not None}

class AutoUpdateSettings(BaseModel):
    enabled: bool = Field(strict=True)


@app.put('/api/v1/admin/control/auto-update', tags=['admin'])
def control_auto_update(payload: AutoUpdateSettings, owner=Depends(require_owner)):
    with control.locked():
        if (control.read('installed.json') or {}).get('version') != 2:
            raise HTTPException(409, 'updater_bootstrap_required')
        control.write('auto-update.json', {'enabled': payload.enabled, 'actor': str(owner['id']), 'at': control.now()})
        pending = control.read('request.json') or {}
        if not payload.enabled and pending.get('actor') == 'automatic':
            (control.root() / 'request.json').unlink(missing_ok=True)
            (control.root() / 'auto-check.json').unlink(missing_ok=True)
    return {'enabled': payload.enabled}


@app.put('/api/v1/admin/control/payments', tags=['admin'])
def control_payments(payload: PaymentSwitches, admin=Depends(require_admin)):
    if payload.stars:
        stars_checkout.credentials()
    if payload.sbp and not sbp_checkout.settings()['configured']:
        raise HTTPException(409, 'sbp_credentials_required')
    with control.locked():
        control.write('payments.json', {'stars': payload.stars, 'sbp': payload.sbp,
                      'sbp_reconciliation': payload.sbp or sbp_reconciliation_enabled(),
                      'actor': str(admin['id']), 'changed_at': control.now()})
    return {'saved': True}


@app.get('/api/v1/admin/control/release', tags=['admin'])
def control_release(response: Response, admin=Depends(require_admin)):
    from .releases import check
    response.headers['Cache-Control'] = 'no-store'
    return check(app.version)

@app.post('/api/v1/admin/control/update', status_code=202, tags=['admin'])
def control_update(admin=Depends(require_admin)):
    if control.read('installed.json') is None:
        raise HTTPException(409, 'updater_bootstrap_required')
    with control.locked():
        status = control.read('status.json', {})
        if control.read('request.json') is not None or status.get('state') == 'running':
            raise HTTPException(409, 'update_already_running')
        control.write('request.json', {'action': 'update', 'actor': str(admin['id']), 'at': control.now()})
    return {'queued': True}

from . import emoji_settings, telegram_binding
from .promotion_report import owner_connection

@app.get('/api/v1/admin/icons', tags=['admin'])
def icons_settings(admin=Depends(require_admin)):
    return {'items': control.read('emoji-catalog.json', []), 'mapping': control.read('emoji-map.json', {})}

@app.post('/api/v1/admin/icons/import', tags=['admin'])
def import_icons(admin=Depends(require_admin)):
    return {'items': emoji_settings.fetch(), 'mapping': control.read('emoji-map.json', {})}

@app.put('/api/v1/admin/icons', tags=['admin'])
def save_icons(payload: dict[str,str], admin=Depends(require_admin)):
    emoji_settings.save(payload)
    return {'saved': True}


from .management import router_for as management_router
app.include_router(management_router(lambda: DATABASE_URL, get_current_user, require_admin, require_operator, normalize_proxy_host))

# Подготовленные в Telegram связи. Доступ только администратору.
class PromotionBindingRequest(BaseModel):
    node_id: UUID
    channel_id: UUID
    ad_tag: str = Field(min_length=32, max_length=32)
    telegram_binding_confirmed: bool

    @field_validator('ad_tag')
    @classmethod
    def tag_valid(cls, value):
        return normalize_ad_tag(value)


@app.get('/api/v1/admin/promotion-bindings', tags=['admin'])
def promotion_bindings(admin: dict = Depends(require_admin)):
    with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as conn:
        runtime = []
        for row in conn.execute('''SELECT n.id,n.name,n.host,n.port,n.status,n.archived_at,n.target_engine,
                r.campaign_id,r.state,r.checked_at,r.plan_revision
                FROM promotion_runtime r JOIN proxy_nodes n ON n.id=r.proxy_node_id''').fetchall():
            runtime.append({'proxy_node_id': row['id'], 'name': row['name'],
                            'campaign_id': row['campaign_id'], 'state': row['state'],
                            **promotion_confirmation(conn, row, row)})
        return {
            'channels': conn.execute('SELECT id,title,telegram_url FROM sponsor_channels WHERE archived_at IS NULL ORDER BY title,id').fetchall(),
            'bindings': conn.execute('''SELECT b.proxy_node_id,b.channel_id,n.name,ch.title,
                right(b.ad_tag,4) AS tag_ending,b.confirmed_at
                FROM channel_promotion_bindings b JOIN proxy_nodes n ON n.id=b.proxy_node_id
                JOIN sponsor_channels ch ON ch.id=b.channel_id
                WHERE n.archived_at IS NULL AND ch.archived_at IS NULL''').fetchall(),
            'runtime': runtime,
            'telegram_automation': telegram_binding.public_status(conn),
            'telegram_owner': owner_connection(),
        }


@app.get('/api/v1/admin/promotion-diagnostics', tags=['admin'])
def read_promotion_diagnostics(admin: dict = Depends(require_admin)):
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row,
                             options='-c statement_timeout=5000') as conn:
            return promotion_diagnostics(conn)
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(503, 'database_unavailable') from exc


@app.put('/api/v1/admin/promotion-bindings', tags=['admin'])
def save_promotion_binding(payload: PromotionBindingRequest, admin: dict = Depends(require_admin)):
    if not payload.telegram_binding_confirmed:
        raise HTTPException(422, 'telegram_binding_confirmation_required')
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as conn:
            # Тот же порядок блокировок, что у планировщика и ответов Telegram.
            if not conn.execute('SELECT pg_try_advisory_xact_lock(736102482) AS ok').fetchone()['ok']:
                raise HTTPException(409, 'promotion_binding_scheduler_busy')
            node=conn.execute('SELECT id,target_engine,host,port FROM proxy_nodes WHERE id=%s AND archived_at IS NULL FOR UPDATE',(payload.node_id,)).fetchone()
            if not node or node['target_engine'] not in (None,'official_mtproxy'):
                raise HTTPException(409, 'classic_node_required')
            channel=conn.execute('SELECT id,telegram_url FROM sponsor_channels WHERE id=%s AND archived_at IS NULL',(payload.channel_id,)).fetchone()
            if not channel: raise HTTPException(404,'channel_not_found')
            job = conn.execute("SELECT id,phase,error_code FROM telegram_binding_jobs WHERE proxy_node_id=%s AND phase<>'done' FOR UPDATE", (payload.node_id,)).fetchone()
            # В inspect внешняя запись ещё не разрешена. Неудачную проверку
            # можно заменить ручным подтверждением; старый ответ worker будет
            # отклонён по job_id. bind/propagating не снимаем даже после ошибки:
            # Telegram мог уже принять переключение канала.
            if job and (job['phase'] != 'inspect' or not job['error_code']):
                raise HTTPException(409, 'promotion_binding_busy')
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,ad_tag,confirmed_by,channel_url,node_host,node_port)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(proxy_node_id,channel_id) DO UPDATE
                SET ad_tag=EXCLUDED.ad_tag,confirmed_by=EXCLUDED.confirmed_by,confirmed_at=now(),channel_url=EXCLUDED.channel_url,node_host=EXCLUDED.node_host,node_port=EXCLUDED.node_port''',
                (payload.node_id,payload.channel_id,payload.ad_tag,admin['id'],channel['telegram_url'],node['host'],node['port']))
            if job:
                conn.execute('DELETE FROM telegram_binding_jobs WHERE id=%s', (job['id'],))
            conn.execute('''INSERT INTO audit_events(actor_user_id,event_type,details_json)
                VALUES(%s,'promotion_binding_confirmed',jsonb_build_object('node_id',%s::text,'channel_id',%s::text,'replaced_inspection_id',%s::text))''',
                (admin['id'],str(payload.node_id),str(payload.channel_id),str(job['id']) if job else None))
    except UniqueViolation as exc:
        raise HTTPException(409,'promotion_tag_already_bound') from exc
    except psycopg.Error as exc:
        explain_license_error(exc)
        raise HTTPException(503, 'database_unavailable') from exc
    return {'saved':True,'tag_ending':payload.ad_tag[-4:]}


@app.delete('/api/v1/admin/promotion-bindings/{node_id}/{channel_id}', tags=['admin'])
def delete_promotion_binding(node_id: UUID, channel_id: UUID, admin: dict = Depends(require_admin)):
    with psycopg.connect(DATABASE_URL,connect_timeout=3) as conn:
        conn.execute('DELETE FROM channel_promotion_bindings WHERE proxy_node_id=%s AND channel_id=%s',(node_id,channel_id))
        conn.execute('''INSERT INTO audit_events(actor_user_id,event_type,details_json)
            VALUES(%s,'promotion_binding_removed',jsonb_build_object('node_id',%s::text,'channel_id',%s::text))''',
            (admin['id'],str(node_id),str(channel_id)))
    return {'removed':True,'message':'Агент вернёт исходный тег при следующей проверке.'}

@app.get('/api/v1/campaigns/{campaign_id}/promotion-status', tags=['campaigns'])
def campaign_promotion_status(campaign_id: UUID, user: dict = Depends(get_current_user)):
    with psycopg.connect(DATABASE_URL,connect_timeout=3,row_factory=dict_row) as conn:
        row=conn.execute('''SELECT c.advertiser_user_id,c.proxy_node_id,c.placement_status,c.channel_id,
            c.auto_schedule_managed AS automatic_assignment,
            c.auto_schedule_tag_pending AS preparation_was_pending,
            c.moderation_status,c.payment_status,c.auto_schedule_paused,c.scheduled_start_at,c.scheduled_end_at,
            EXISTS(SELECT 1 FROM channel_promotion_bindings b
            JOIN proxy_nodes n ON n.id=b.proxy_node_id JOIN sponsor_channels ch ON ch.id=b.channel_id
            WHERE (c.proxy_node_id IS NULL OR b.proxy_node_id=c.proxy_node_id) AND b.channel_id=c.channel_id
            AND b.channel_url=ch.telegram_url AND ch.archived_at IS NULL
            AND (b.node_host,b.node_port)=(n.host,n.port)
            AND n.archived_at IS NULL AND n.status='active'
            AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')) AS prepared,
            r.campaign_id,r.state,r.checked_at,r.plan_revision
            FROM campaigns c LEFT JOIN promotion_runtime r ON r.proxy_node_id=c.proxy_node_id
            WHERE c.id=%s''',(campaign_id,)).fetchone()
        if not row or (user['role'] not in ('admin','operator') and row['advertiser_user_id']!=user['id']):
            raise HTTPException(404,'campaign_not_found')
        row['telegram_binding_pending'] = bool(conn.execute("SELECT 1 FROM telegram_binding_jobs WHERE campaign_id=%s AND phase<>'done'", (campaign_id,)).fetchone())
        row['binding_selection_pending'] = False
        if (row['placement_status']=='scheduled' and row['automatic_assignment'] and not row['auto_schedule_paused']
                and (not row['prepared'] or row['preparation_was_pending'])
                and not (row['campaign_id']==campaign_id and row['state']=='applied')):
            row['binding_selection_pending'] = bool(conn.execute('''SELECT 1 FROM channel_promotion_bindings b
                JOIN proxy_nodes n ON n.id=b.proxy_node_id JOIN sponsor_channels ch ON ch.id=b.channel_id
                WHERE b.channel_id=%s AND b.channel_url=ch.telegram_url AND ch.archived_at IS NULL
                  AND n.status='active' AND n.archived_at IS NULL
                  AND (n.target_engine IS NULL OR n.target_engine='official_mtproxy')
                  AND (b.node_host,b.node_port)=(n.host,n.port)
                  AND NOT EXISTS(SELECT 1 FROM placement_observations WHERE campaign_id=%s)
                LIMIT 1''',(row['channel_id'],campaign_id)).fetchone())
        node=conn.execute('SELECT id,host,port,status,archived_at,target_engine FROM proxy_nodes WHERE id=%s',
                          (row['proxy_node_id'],)).fetchone()
        result=promotion_confirmation(conn, node, row)
        result['config_applied'] = bool(result['config_applied'] and row['campaign_id']==campaign_id)
        now=datetime.now(timezone.utc)
        return {'prepared':row['prepared'], **result,
                'automation_status':campaign_automation_status(row,result,node,now=now),
                'placement_status':row['placement_status'],
                'scheduled_start_at':row['scheduled_start_at'],
                'scheduled_end_at':row['scheduled_end_at'], 'server_time':now}

from .team import router_for as team_router
app.include_router(team_router(lambda: DATABASE_URL, require_admin, require_operator))

from .default_sponsor import router_for as default_sponsor_router
app.include_router(default_sponsor_router(lambda: DATABASE_URL, require_admin))

from .campaign_notices import router as campaign_notice_router
app.include_router(campaign_notice_router)

from .growth import router_for as growth_router_for
app.include_router(growth_router_for(lambda: DATABASE_URL, get_current_user, require_admin))

from .wallet import router_for as wallet_router_for
app.include_router(wallet_router_for(lambda: DATABASE_URL, get_current_user, require_admin))

from .sbp_connection import router_for as sbp_connection_router
app.include_router(sbp_connection_router(lambda: DATABASE_URL, require_owner))

from .channel_logos import router_for as channel_logos_router
app.include_router(channel_logos_router(lambda: DATABASE_URL, get_current_user))

from .campaign_pricing import router_for as campaign_pricing_router
app.include_router(campaign_pricing_router(lambda: DATABASE_URL, require_admin))

from .user_profiles import router_for as user_profiles_router
app.include_router(user_profiles_router(lambda: DATABASE_URL, get_current_user))

from .admin_users import router_for as admin_users_router
app.include_router(admin_users_router(lambda: DATABASE_URL, require_operator))

from .application_licenses import router_for as application_license_router, start_client as start_license_client
app.include_router(application_license_router(lambda: DATABASE_URL, get_current_user, require_admin))
_license_stop = None

@app.on_event('startup')
def application_license_startup():
    global _license_stop
    _license_stop = start_license_client(DATABASE_URL)

@app.on_event('shutdown')
def application_license_shutdown():
    if _license_stop is not None:
        _license_stop.set()

from .channel_directory import router_for as channel_directory_router
app.include_router(channel_directory_router(lambda: DATABASE_URL, get_current_user))

from .channel_ai_connection import router_for as channel_ai_router
app.include_router(channel_ai_router(lambda: DATABASE_URL, require_owner))
from .proxy_provisioning import router_for as proxy_provisioning_router
app.include_router(proxy_provisioning_router(lambda: DATABASE_URL, require_owner))
from .campaign_assistant import router_for as campaign_assistant_router
app.include_router(campaign_assistant_router(lambda: DATABASE_URL, require_operator, campaign_promotion_status))

from .campaign_analytics import router_for as campaign_analytics_router
app.include_router(campaign_analytics_router(lambda: DATABASE_URL,get_current_user))


from . import home_banner
@app.get('/api/v1/admin/home-banner')
def get_home_banner(admin=Depends(require_admin)):
    return home_banner.read()

@app.put('/api/v1/admin/home-banner')
def set_home_banner(data: home_banner.Banner, admin=Depends(require_admin)):
    try:
        return home_banner.save(data)
    except OSError as exc:
        raise HTTPException(status_code=503, detail='Не удалось сохранить текст главной') from exc

from .verified_profiles import router_for as verified_profiles_router
app.include_router(verified_profiles_router(lambda: DATABASE_URL, get_current_user, require_admin))

from .hall_of_fame import router_for as hall_router
app.include_router(hall_router(lambda: DATABASE_URL, get_current_user, require_admin))

from .community import router_for as community_router
app.include_router(community_router(lambda: DATABASE_URL,get_current_user))

from .channel_subscription import router_for as channel_subscription_router
app.include_router(channel_subscription_router(get_current_user, require_admin))

from .proxy_gateway import router_for as proxy_gateway_router
app.include_router(proxy_gateway_router(lambda: DATABASE_URL, get_current_user, require_owner))

from .refunds import router_for as refunds_router
app.include_router(refunds_router(lambda: DATABASE_URL, require_admin))

from .channel_blocks import router_for as channel_blocks_router
app.include_router(channel_blocks_router(lambda: DATABASE_URL, require_operator))

from .flow_account import router_for as flow_account_router, FlowNoStoreMiddleware
app.add_middleware(FlowNoStoreMiddleware)
app.include_router(flow_account_router(
    lambda: DATABASE_URL,
    lambda: _required_secret(SESSION_SIGNING_KEY, 'SESSION_SIGNING_KEY'),
    get_current_user,
))
