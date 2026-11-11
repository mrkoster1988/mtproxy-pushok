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
from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.errors import ExclusionViolation, UniqueViolation
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
from .launch_readiness import build_launch_readiness
from . import sbp_checkout
from .sbp_webhook import router as sbp_webhook_router
from .sbp_provider import SBPProviderError
from .sbp_reconcile import SBPReconcileError
from .sbp_verification import SBPVerificationError

app = FastAPI(
    title="MTProxy Pushok API",
    version="0.050",
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


class TariffAdminResponse(TariffResponse):
    is_active: bool
    stars_price_xtr: int | None = None


class TariffSettingsUpdate(BaseModel):
    """Редактируем тариф, не меняя уже зафиксированные цены заказов."""
    name: str = Field(min_length=1, max_length=100)
    duration_seconds: int = Field(ge=3600, le=31536000)
    price_kopecks: int = Field(ge=100, le=100000000)
    stars_price_xtr: int | None = Field(ge=1, le=1000000)
    is_active: bool

    @field_validator("name")
    @classmethod
    def normalize_tariff_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("tariff_name_required")
        return value


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


class CampaignResponse(BaseModel):
    id: UUID
    channel_id: UUID
    tariff_id: UUID
    tariff_name_snapshot: str
    duration_seconds_snapshot: int
    price_kopecks_snapshot: int
    currency_snapshot: str
    moderation_status: str
    payment_status: str
    placement_status: str
    requested_start_at: datetime | None
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    moderator_comment: str | None
    payment_review_comment: str | None = None
    channel_title: str | None = None
    channel_url: str | None = None


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
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    comment: str | None = Field(default=None, max_length=2000)


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
                SELECT u.id, u.telegram_user_id, u.role, u.first_name, u.last_name,
                       u.username, s.id AS session_id
                FROM users AS u
                JOIN user_sessions AS s ON s.user_id = u.id
                WHERE u.id = %s AND s.id = %s
                  AND s.revoked_at IS NULL AND s.expires_at > now()
                """,
                (user_id, session_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="session_revoked_or_expired")
    return user


def require_operator(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="operator_role_required")
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin_role_required")
    return user


def _campaign_response(row: dict[str, Any]) -> CampaignResponse:
    if row.get("currency_snapshot") is not None:
        row["currency_snapshot"] = row["currency_snapshot"].strip()
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
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"status": "ready", "database": "ok"}


@app.post("/api/v1/auth/telegram", response_model=SessionResponse, tags=["authentication"])
def telegram_login(payload: TelegramLoginPayload) -> SessionResponse:
    _verify_telegram_login(payload)
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
                    RETURNING id
                    """,
                    (payload.id, payload.first_name, payload.last_name, payload.username),
                ).fetchone()
                cursor.execute(
                    """
                    INSERT INTO user_sessions (id, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (session_id, user["id"], expires_at),
                )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc

    return SessionResponse(
        access_token=_create_session_token(user["id"], session_id),
        expires_in=SESSION_TTL_SECONDS,
    )


@app.get("/api/v1/me", response_model=UserResponse, tags=["account"])
def get_me(user: dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    return UserResponse(**user)


@app.post("/api/v1/auth/logout", status_code=204, tags=["authentication"])
def logout(user: dict[str, Any] = Depends(get_current_user)) -> None:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at = now() WHERE id = %s AND user_id = %s",
                (user["session_id"], user["id"]),
            )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc


@app.get("/api/v1/tariffs", response_model=list[TariffResponse], tags=["tariffs"])
def list_tariffs() -> list[TariffResponse]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
            rows = connection.execute(
                """
                SELECT id, name, duration_seconds, price_kopecks, currency
                FROM tariffs
                WHERE is_active = TRUE
                ORDER BY duration_seconds, price_kopecks
                """
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [
        TariffResponse(
            id=row[0], name=row[1], duration_seconds=row[2],
            price_kopecks=row[3], currency=row[4].strip(),
        )
        for row in rows
    ]


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
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                INSERT INTO sponsor_channels (owner_user_id, telegram_url, title)
                VALUES (%s, %s, %s)
                RETURNING id, telegram_url, title
                """,
                (user["id"], payload.telegram_url.rstrip("/"), normalized_title),
            ).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="channel_already_added") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return ChannelResponse(**row)


@app.post("/api/v1/campaigns", response_model=CampaignResponse, status_code=201, tags=["campaigns"])
def create_campaign(
    payload: CampaignCreate,
    user: dict[str, Any] = Depends(get_current_user),
) -> CampaignResponse:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                INSERT INTO campaigns (
                    advertiser_user_id, channel_id, tariff_id,
                    tariff_name_snapshot, duration_seconds_snapshot,
                    price_kopecks_snapshot, currency_snapshot, requested_start_at
                )
                SELECT %s, c.id, t.id, t.name, t.duration_seconds,
                       t.price_kopecks, t.currency, %s
                FROM sponsor_channels AS c
                JOIN tariffs AS t ON t.id = %s AND t.is_active = TRUE
                WHERE c.id = %s AND c.owner_user_id = %s
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (user["id"], payload.requested_start_at, payload.tariff_id,
                 payload.channel_id, user["id"]),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="channel_or_active_tariff_not_found")
    return _campaign_response(row)


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
                       c.duration_seconds_snapshot, c.price_kopecks_snapshot,
                       c.currency_snapshot, c.moderation_status, c.payment_status,
                       c.placement_status, c.requested_start_at, c.scheduled_start_at,
                       c.scheduled_end_at, c.moderator_comment,
                       latest_proof.review_comment AS payment_review_comment
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                LEFT JOIN LATERAL (
                    SELECT p.review_comment
                    FROM payment_proofs AS p
                    WHERE p.campaign_id = c.id AND p.reviewed_at IS NOT NULL
                    ORDER BY p.submitted_at DESC
                    LIMIT 1
                ) AS latest_proof ON TRUE
                WHERE c.advertiser_user_id = %s
                ORDER BY c.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    except psycopg.Error as exc:
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
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            channel = connection.execute(
                """
                SELECT ch.id FROM sponsor_channels AS ch
                JOIN campaigns AS c ON c.channel_id = ch.id
                WHERE c.id = %s AND c.advertiser_user_id = %s
                FOR UPDATE OF ch
                """, (campaign_id, user["id"]),
            ).fetchone()
            if channel is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            row = connection.execute(
                """
                UPDATE campaigns
                SET moderation_status = 'pending', updated_at = now()
                WHERE id = %s AND advertiser_user_id = %s AND moderation_status IN ('draft', 'changes_requested')
                  AND payment_status = 'not_required' AND placement_status = 'not_scheduled'
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
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


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
    if payload.decision != "approved" and not (payload.comment and payload.comment.strip()):
        raise HTTPException(status_code=422, detail="moderation_comment_required")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE campaigns
                SET moderation_status = %s,
                    payment_status = CASE WHEN %s = 'approved'
                                          THEN 'awaiting_payment' ELSE payment_status END,
                    moderator_comment = %s,
                    updated_at = now()
                WHERE id = %s AND moderation_status = 'pending'
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (payload.decision, payload.decision,
                 payload.comment.strip() if payload.comment else None, campaign_id),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 FROM campaigns WHERE id = %s", (campaign_id,)
                ).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="campaign_not_found")
                raise HTTPException(status_code=409, detail="campaign_not_pending")
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'campaign_moderated', jsonb_build_object('decision', %s::text))
                """,
                (operator["id"], campaign_id, payload.decision),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


def _receipt_metadata(upload: UploadFile) -> tuple[bytes, str, str, str]:
    data = upload.file.read(MAX_PAYMENT_PROOF_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="payment_proof_empty")
    if len(data) > MAX_PAYMENT_PROOF_BYTES:
        raise HTTPException(status_code=413, detail="payment_proof_too_large")
    signatures = (
        (bytes([0xFF, 0xD8, 0xFF]), "image/jpeg", ".jpg"),
        (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), "image/png", ".png"),
        (b"%PDF-", "application/pdf", ".pdf"),
    )
    for signature, content_type, suffix in signatures:
        if data.startswith(signature):
            if upload.content_type != content_type:
                raise HTTPException(status_code=415, detail="payment_proof_content_type_mismatch")
            original_name = os.path.basename(upload.filename or "payment-proof")
            original_name = "".join(ch for ch in original_name if ch.isprintable())[:255]
            return data, content_type, original_name or "payment-proof", suffix
    raise HTTPException(status_code=415, detail="unsupported_payment_proof_format")


def _store_payment_proof(data: bytes, suffix: str) -> tuple[str, str]:
    storage_key = f"{uuid4().hex}{suffix}"
    os.makedirs(PAYMENT_PROOFS_DIR, mode=0o700, exist_ok=True)
    os.chmod(PAYMENT_PROOFS_DIR, 0o700)
    full_path = os.path.join(PAYMENT_PROOFS_DIR, storage_key)
    descriptor = os.open(full_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    except OSError:
        if os.path.exists(full_path):
            os.unlink(full_path)
        raise
    return storage_key, full_path


@app.post("/api/v1/campaigns/{campaign_id}/payment-proofs", response_model=PaymentProofResponse, status_code=201, tags=["payments"])
def upload_payment_proof(
    campaign_id: UUID,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(get_current_user),
) -> PaymentProofResponse:
    data, content_type, original_name, suffix = _receipt_metadata(file)
    stored_path = None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """
                SELECT id, payment_status
                FROM campaigns
                WHERE id = %s AND advertiser_user_id = %s
                  AND moderation_status = 'approved'
                FOR UPDATE
                """,
                (campaign_id, user["id"]),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            if campaign["payment_status"] not in {"awaiting_payment", "rejected"}:
                raise HTTPException(status_code=409, detail="payment_proof_not_expected")
            if connection.execute("""SELECT 1 FROM payment_attempts WHERE campaign_id=%s
                                      AND provider='sbp' AND status IN ('created','pending') LIMIT 1""",
                                  (campaign_id,)).fetchone():
                raise HTTPException(409, 'another_payment_in_progress')

            storage_key, stored_path = _store_payment_proof(data, suffix)
            proof = connection.execute(
                """
                INSERT INTO payment_proofs (
                    campaign_id, file_storage_key, original_file_name,
                    content_type, size_bytes, submitted_by_user_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, original_file_name, content_type, size_bytes, submitted_at
                """,
                (campaign_id, storage_key, original_name, content_type, len(data), user["id"]),
            ).fetchone()
            connection.execute(
                "UPDATE campaigns SET payment_status = 'proof_submitted', updated_at = now() WHERE id = %s",
                (campaign_id,),
            )
            connection.execute(
                "INSERT INTO audit_events (actor_user_id, campaign_id, event_type) VALUES (%s, %s, 'payment_proof_submitted')",
                (user["id"], campaign_id),
            )
    except HTTPException:
        if stored_path and os.path.exists(stored_path):
            os.unlink(stored_path)
        raise
    except OSError as exc:
        if stored_path and os.path.exists(stored_path):
            os.unlink(stored_path)
        raise HTTPException(status_code=503, detail="payment_proof_storage_unavailable") from exc
    except psycopg.Error as exc:
        if stored_path and os.path.exists(stored_path):
            os.unlink(stored_path)
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return PaymentProofResponse(**proof)


@app.get("/api/v1/operator/campaigns/{campaign_id}/payment-proofs/{proof_id}", tags=["operator"])
def download_payment_proof(
    campaign_id: UUID,
    proof_id: UUID,
    operator: dict[str, Any] = Depends(require_operator),
) -> FileResponse:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            proof = connection.execute(
                "SELECT file_storage_key, original_file_name, content_type FROM payment_proofs WHERE id = %s AND campaign_id = %s",
                (proof_id, campaign_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if proof is None:
        raise HTTPException(status_code=404, detail="payment_proof_not_found")
    storage_key = proof["file_storage_key"]
    if os.path.basename(storage_key) != storage_key or not storage_key.endswith((".jpg", ".png", ".pdf")):
        raise HTTPException(status_code=404, detail="payment_proof_not_found")
    full_path = os.path.join(PAYMENT_PROOFS_DIR, storage_key)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="payment_proof_file_missing")
    return FileResponse(
        full_path,
        media_type=proof["content_type"],
        filename=proof["original_file_name"],
        headers={"Cache-Control": "private, no-store"},
    )


@app.post("/api/v1/operator/campaigns/{campaign_id}/payment-review", response_model=CampaignResponse, tags=["operator"])
def review_payment(
    campaign_id: UUID,
    payload: PaymentReviewRequest,
    operator: dict[str, Any] = Depends(require_operator),
) -> CampaignResponse:
    if payload.decision == "rejected" and not (payload.comment and payload.comment.strip()):
        raise HTTPException(status_code=422, detail="payment_review_comment_required")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                "SELECT id, payment_status FROM campaigns WHERE id = %s FOR UPDATE",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            if campaign["payment_status"] != "proof_submitted":
                raise HTTPException(status_code=409, detail="payment_proof_not_pending_review")
            proof = connection.execute(
                """
                SELECT id FROM payment_proofs
                WHERE campaign_id = %s AND reviewed_at IS NULL
                ORDER BY submitted_at DESC LIMIT 1 FOR UPDATE
                """,
                (campaign_id,),
            ).fetchone()
            if proof is None:
                raise HTTPException(status_code=409, detail="payment_proof_not_found")
            comment = payload.comment.strip() if payload.comment else None
            connection.execute(
                """
                UPDATE payment_proofs
                SET reviewed_by_user_id = %s, review_comment = %s, reviewed_at = now()
                WHERE id = %s
                """,
                (operator["id"], comment, proof["id"]),
            )
            row = connection.execute(
                """
                UPDATE campaigns SET payment_status = %s, updated_at = now()
                WHERE id = %s
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (payload.decision, campaign_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'payment_reviewed', jsonb_build_object('decision', %s::text))
                """,
                (operator["id"], campaign_id, payload.decision),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


@app.post("/api/v1/campaigns/{campaign_id}/slot-hold", status_code=201, tags=["calendar"])
def create_slot_hold(
    campaign_id: UUID,
    payload: SlotHoldRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """15-минутная бронь перед оплатой, без списания или изменения MTProxy."""
    if payload.start_at.tzinfo is None or payload.start_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="slot_start_requires_timezone")
    if payload.start_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="slot_start_must_be_future")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """SELECT id, duration_seconds_snapshot, moderation_status,
                          payment_status, placement_status
                   FROM campaigns WHERE id = %s AND advertiser_user_id = %s FOR UPDATE""",
                (campaign_id, user["id"]),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            if (
                campaign["moderation_status"] != "approved"
                or campaign["payment_status"] not in ("awaiting_payment", "proof_submitted", "rejected")
                or campaign["placement_status"] != "not_scheduled"
            ):
                raise HTTPException(status_code=409, detail="campaign_not_holdable")
            start, end, expires = normalize_hold_window(
                payload.start_at, campaign["duration_seconds_snapshot"]
            )
            node = connection.execute(
                "SELECT id FROM proxy_nodes WHERE id = %s AND status = 'active' FOR UPDATE",
                (payload.proxy_node_id,),
            ).fetchone()
            if node is None:
                raise HTTPException(status_code=404, detail="active_proxy_node_not_found")
            connection.execute(
                "DELETE FROM slot_holds WHERE proxy_node_id = %s AND expires_at <= now()",
                (payload.proxy_node_id,),
            )
            occupied = connection.execute(
                """SELECT 1 FROM campaigns
                   WHERE proxy_node_id = %s AND placement_status IN ('scheduled','active')
                     AND tstzrange(scheduled_start_at, scheduled_end_at, '[)')
                         && tstzrange(%s, %s, '[)')
                   LIMIT 1""",
                (payload.proxy_node_id, start, end),
            ).fetchone()
            if occupied is not None:
                raise HTTPException(status_code=409, detail="proxy_node_slot_unavailable")
            # Старую бронь этого рекламодателя разрешено заменить атомарно.
            connection.execute("DELETE FROM slot_holds WHERE campaign_id = %s", (campaign_id,))
            hold = connection.execute(
                """INSERT INTO slot_holds
                     (campaign_id, proxy_node_id, starts_at, ends_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, starts_at, ends_at, expires_at""",
                (campaign_id, payload.proxy_node_id, start, end, expires),
            ).fetchone()
            connection.execute(
                """INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                   VALUES (%s, %s, 'slot_hold_created',
                           jsonb_build_object('proxy_node_id', %s::text,
                                              'expires_at', %s::text))""",
                (user["id"], campaign_id, str(payload.proxy_node_id), expires.isoformat()),
            )
    except ExclusionViolation as exc:
        raise HTTPException(status_code=409, detail="slot_held_by_another_campaign") from exc
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="slot_hold_conflict") from exc
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {"id": hold["id"], "campaign_id": campaign_id, "proxy_node_id": payload.proxy_node_id,
            "starts_at": hold["starts_at"], "ends_at": hold["ends_at"],
            "expires_at": hold["expires_at"], "payment_verified": False,
            "placement_started": False}



@app.get("/api/v1/campaigns/{campaign_id}/slot-options", tags=["calendar"])
def list_campaign_slot_options(
    campaign_id: UUID,
    start_at: datetime,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Read-only подбор узлов для своего заказа; адреса и секреты не выдаются.

    Итоговую бронь проверяет POST /slot-hold под блокировкой узла.
    """
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="slot_start_requires_timezone")
    start_utc = start_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if start_utc <= now or start_utc > now + timedelta(days=90):
        raise HTTPException(status_code=422, detail="slot_start_out_of_range")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """SELECT duration_seconds_snapshot, moderation_status,
                          payment_status, placement_status
                   FROM campaigns
                   WHERE id = %s AND advertiser_user_id = %s""",
                (campaign_id, user["id"]),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            if (
                campaign["moderation_status"] != "approved"
                or campaign["payment_status"] not in ("awaiting_payment", "proof_submitted", "rejected")
                or campaign["placement_status"] != "not_scheduled"
            ):
                raise HTTPException(status_code=409, detail="campaign_not_holdable")
            end_utc = start_utc + timedelta(seconds=campaign["duration_seconds_snapshot"])
            rows = connection.execute(
                """SELECT n.id FROM proxy_nodes AS n
                   WHERE n.status = 'active'
                     AND NOT EXISTS (
                        SELECT 1 FROM campaigns AS c
                        WHERE c.proxy_node_id = n.id
                          AND c.placement_status IN ('scheduled', 'active')
                          AND tstzrange(c.scheduled_start_at, c.scheduled_end_at, '[)')
                              && tstzrange(%s, %s, '[)')
                     )
                     AND NOT EXISTS (
                        SELECT 1 FROM slot_holds AS h
                        WHERE h.proxy_node_id = n.id AND h.expires_at > now()
                          AND h.campaign_id <> %s
                          AND tstzrange(h.starts_at, h.ends_at, '[)')
                              && tstzrange(%s, %s, '[)')
                     )
                   ORDER BY n.id LIMIT 20""",
                (start_utc, end_utc, campaign_id, start_utc, end_utc),
            ).fetchall()
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {
        "campaign_id": campaign_id,
        "start_at": start_utc,
        "end_at": end_utc,
        "nodes": [{"id": row["id"], "title": f"Прокси №{index + 1}"}
                  for index, row in enumerate(rows)],
        "reservation_created": False,
        "note": "Доступность справочная; при удержании слот проверяется повторно.",
    }


@app.get("/api/v1/campaigns/{campaign_id}/slot-hold", tags=["calendar"])
def get_slot_hold(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Статус личной брони: 404 для чужой кампании и истекшего удержания."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT h.id, h.campaign_id, h.proxy_node_id,
                          h.starts_at, h.ends_at, h.expires_at,
                          GREATEST(0, CEIL(EXTRACT(EPOCH FROM (h.expires_at - now()))))::int
                              AS remaining_seconds
                   FROM slot_holds AS h
                   JOIN campaigns AS c ON c.id = h.campaign_id
                   WHERE h.campaign_id = %s AND c.advertiser_user_id = %s
                     AND h.expires_at > now()""",
                (campaign_id, user["id"]),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="active_slot_hold_not_found")
    return {
        "id": row["id"], "campaign_id": row["campaign_id"],
        "proxy_node_id": row["proxy_node_id"],
        "starts_at": row["starts_at"], "ends_at": row["ends_at"],
        "expires_at": row["expires_at"],
        "remaining_seconds": max(0, int(row["remaining_seconds"])),
        "payment_verified": False, "placement_started": False,
    }


@app.delete("/api/v1/campaigns/{campaign_id}/slot-hold", status_code=204, tags=["calendar"])
def cancel_slot_hold(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                "SELECT id FROM campaigns WHERE id = %s AND advertiser_user_id = %s FOR UPDATE",
                (campaign_id, user["id"]),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            connection.execute("DELETE FROM slot_holds WHERE campaign_id = %s", (campaign_id,))
            connection.execute(
                "INSERT INTO audit_events (actor_user_id, campaign_id, event_type) VALUES (%s, %s, 'slot_hold_cancelled')",
                (user["id"], campaign_id),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return Response(status_code=204)


@app.post("/api/v1/operator/campaigns/{campaign_id}/schedule", response_model=CampaignResponse, tags=["operator"])
def schedule_placement(
    campaign_id: UUID,
    payload: PlacementScheduleRequest,
    operator: dict[str, Any] = Depends(require_operator),
) -> CampaignResponse:
    if payload.start_at.tzinfo is None or payload.start_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="schedule_start_requires_timezone")
    start_at = payload.start_at.astimezone(timezone.utc)
    if start_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="schedule_start_must_be_future")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """
                SELECT id, duration_seconds_snapshot, moderation_status,
                       payment_status, placement_status
                FROM campaigns WHERE id = %s FOR UPDATE
                """,
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            if (
                campaign["moderation_status"] != "approved"
                or campaign["payment_status"] != "verified"
                or campaign["placement_status"] != "not_scheduled"
            ):
                raise HTTPException(status_code=409, detail="campaign_not_ready_for_placement")
            node = connection.execute(
                "SELECT id FROM proxy_nodes WHERE id = %s AND status = 'active' FOR UPDATE",
                (payload.proxy_node_id,),
            ).fetchone()
            if node is None:
                raise HTTPException(status_code=404, detail="active_proxy_node_not_found")
            end_at = start_at + timedelta(seconds=campaign["duration_seconds_snapshot"])
            connection.execute(
                "DELETE FROM slot_holds WHERE proxy_node_id = %s AND expires_at <= now()",
                (payload.proxy_node_id,),
            )
            other_hold = connection.execute(
                """SELECT 1 FROM slot_holds
                   WHERE proxy_node_id = %s AND campaign_id <> %s AND expires_at > now()
                     AND tstzrange(starts_at, ends_at, '[)')
                         && tstzrange(%s, %s, '[)')
                   LIMIT 1""",
                (payload.proxy_node_id, campaign_id, start_at, end_at),
            ).fetchone()
            if other_hold is not None:
                raise HTTPException(status_code=409, detail="slot_held_by_another_campaign")
            connection.execute("DELETE FROM slot_holds WHERE campaign_id = %s", (campaign_id,))
            row = connection.execute(
                """
                UPDATE campaigns
                SET proxy_node_id = %s, scheduled_start_at = %s,
                    scheduled_end_at = %s, placement_status = 'scheduled',
                    updated_at = now()
                WHERE id = %s
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (payload.proxy_node_id, start_at, end_at, campaign_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                VALUES (%s, %s, 'placement_scheduled',
                        jsonb_build_object('proxy_node_id', %s::text, 'start_at', %s::text, 'end_at', %s::text))
                """,
                (operator["id"], campaign_id, str(payload.proxy_node_id), start_at.isoformat(), end_at.isoformat()),
            )
    except ExclusionViolation as exc:
        raise HTTPException(status_code=409, detail="proxy_node_slot_unavailable") from exc
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


@app.post("/api/v1/operator/campaigns/{campaign_id}/display-observations", status_code=201, tags=["operator"])
def report_placement_observation(
    campaign_id: UUID,
    payload: PlacementObservationRequest,
    operator: dict[str, Any] = Depends(require_operator),
) -> dict[str, Any]:
    """Заявление оператора о визуальной проверке в независимом клиенте Telegram."""
    if payload.observed_at.tzinfo is None or payload.observed_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="observation_timezone_required")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """SELECT id, moderation_status, payment_status, placement_status,
                          scheduled_start_at, scheduled_end_at
                   FROM campaigns WHERE id = %s FOR UPDATE""",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            now = datetime.now(timezone.utc)
            if (
                campaign["moderation_status"] != "approved"
                or campaign["payment_status"] != "verified"
                or campaign["placement_status"] != "scheduled"
                or campaign["scheduled_start_at"] is None
                or campaign["scheduled_end_at"] is None
                or not (campaign["scheduled_start_at"] <= now < campaign["scheduled_end_at"])
            ):
                raise HTTPException(status_code=409, detail="placement_not_ready_for_observation")
            try:
                validate_observation_window(
                    campaign["scheduled_start_at"], campaign["scheduled_end_at"],
                    payload.observed_at, now,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            report = connection.execute(
                """INSERT INTO placement_observations
                     (campaign_id, observer_user_id, result, method, observed_at, note)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, result, method, observed_at, recorded_at""",
                (campaign_id, operator["id"], payload.result, payload.method,
                 payload.observed_at, payload.note),
            ).fetchone()
            connection.execute(
                """INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                   VALUES (%s, %s, 'placement_observation_recorded',
                           jsonb_build_object('observation_id', %s::bigint, 'result', %s::text))""",
                (operator["id"], campaign_id, report["id"], payload.result),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {
        "id": report["id"], "campaign_id": campaign_id,
        "result": report["result"], "method": report["method"],
        "observed_at": report["observed_at"], "recorded_at": report["recorded_at"],
        "operator_report_only": True, "telegram_api_verified": False,
    }


@app.post("/api/v1/operator/campaigns/{campaign_id}/display-observations/{observation_id}/evidence", status_code=201, tags=["operator"])
def upload_placement_evidence(
    campaign_id: UUID,
    observation_id: int,
    file: UploadFile = File(...),
    operator: dict[str, Any] = Depends(require_operator),
) -> dict[str, Any]:
    """Фото может прикрепить только автор своего отчёта в течение 24 часов."""
    try:
        data, content_type, suffix, digest = sanitize_image(file)
    except ValueError as exc:
        code = str(exc)
        status_code = 413 if code == "evidence_too_large" else 415 if code in ("unsupported_evidence_format", "evidence_format_mismatch") else 422
        raise HTTPException(status_code=status_code, detail=code) from exc
    stored_path = None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT id FROM placement_observations
                   WHERE id = %s AND campaign_id = %s AND observer_user_id = %s
                     AND recorded_at > now() - interval '24 hours' FOR UPDATE""",
                (observation_id, campaign_id, operator["id"]),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="evidence_observation_unavailable")
            key, stored_path = store_image(data, suffix, PLACEMENT_EVIDENCE_DIR)
            recorded = connection.execute(
                """INSERT INTO placement_evidence
                    (observation_id, uploader_user_id, storage_key, content_type, size_bytes, sha256)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, observation_id, size_bytes, sha256, created_at""",
                (observation_id, operator["id"], key, content_type, len(data), digest),
            ).fetchone()
            connection.execute(
                """INSERT INTO audit_events(actor_user_id, campaign_id, event_type, details_json)
                   VALUES (%s, %s, 'placement_evidence_uploaded',
                           jsonb_build_object('observation_id', %s::bigint, 'sha256', %s::text))""",
                (operator["id"], campaign_id, observation_id, digest),
            )
    except (HTTPException, OSError, psycopg.Error) as exc:
        if stored_path and os.path.isfile(stored_path):
            os.unlink(stored_path)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, UniqueViolation):
            raise HTTPException(status_code=409, detail="evidence_already_attached") from exc
        if isinstance(exc, psycopg.Error):
            raise HTTPException(status_code=503, detail="database_unavailable") from exc
        raise HTTPException(status_code=503, detail="evidence_storage_unavailable") from exc
    return {**dict(recorded), "operator_provided": True, "telegram_api_verified": False}


@app.get("/api/v1/operator/campaigns/{campaign_id}/display-observations/{observation_id}/evidence", tags=["operator"])
def download_placement_evidence(
    campaign_id: UUID,
    observation_id: int,
    operator: dict[str, Any] = Depends(require_operator),
) -> Response:
    """Доступно только оператору с действующей сессией."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT e.storage_key, e.content_type, e.sha256, e.size_bytes FROM placement_evidence AS e
                   JOIN placement_observations AS o ON o.id = e.observation_id
                   WHERE o.campaign_id = %s AND o.id = %s""",
                (campaign_id, observation_id),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="evidence_not_found")
    try:
        data = read_verified_image(
            PLACEMENT_EVIDENCE_DIR, row["storage_key"], row["sha256"],
            row["size_bytes"], row["content_type"],
        )
    except EvidenceReadError as exc:
        code = str(exc)
        status_code = 404 if code == "evidence_file_missing" else 503 if code == "evidence_storage_unavailable" else 409
        raise HTTPException(status_code=status_code, detail=code) from exc
    key = row["storage_key"]
    return Response(
        content=data, media_type=row["content_type"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="sponsor-observation-' + key + '"',
        },
    )


@app.get("/api/v1/admin/placement-evidence/integrity", tags=["admin"])
def inspect_placement_evidence(
    limit: int = 50,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Сверить последние файлы с БД без изменения хранилища."""
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="invalid_integrity_limit")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT id, observation_id, storage_key, content_type, sha256, size_bytes
                   FROM placement_evidence ORDER BY created_at DESC, id DESC LIMIT %s""",
                (limit,),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    issues = []
    for row in rows:
        try:
            read_verified_image(
                PLACEMENT_EVIDENCE_DIR, row["storage_key"], row["sha256"],
                row["size_bytes"], row["content_type"],
            )
        except EvidenceReadError as exc:
            issues.append({
                "evidence_id": row["id"],
                "observation_id": row["observation_id"],
                "issue": str(exc),
            })
    return {
        "checked": len(rows), "healthy": len(rows) - len(issues),
        "issues": issues, "limit": limit,
        "changes_applied": False, "telegram_api_verified": False,
        "scope": "stored_file_integrity_only",
    }


@app.get("/api/v1/operator/campaigns/{campaign_id}/display-observations", tags=["operator"])
def list_placement_observations(
    campaign_id: UUID,
    operator: dict[str, Any] = Depends(require_operator),
) -> list[dict[str, Any]]:
    """Последние 50 отчётов: полный журнал доступен только оператору."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                "SELECT id FROM campaigns WHERE id = %s", (campaign_id,),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            rows = connection.execute(
                """SELECT o.id, o.observer_user_id, o.result, o.method, o.observed_at,
                          o.note, o.recorded_at, e.id AS evidence_id, e.sha256 AS evidence_sha256
                   FROM placement_observations AS o
                   LEFT JOIN placement_evidence AS e ON e.observation_id = o.id
                   WHERE o.campaign_id = %s ORDER BY o.id DESC LIMIT 50""",
                (campaign_id,),
            ).fetchall()
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [
        {**dict(row), "operator_report_only": True, "telegram_api_verified": False}
        for row in rows
    ]


@app.get("/api/v1/campaigns/{campaign_id}/report", tags=["campaigns"])
def get_campaign_report(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Отчёт для владельца кампании: только whitelist событий и сводка наблюдений."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            campaign = connection.execute(
                """SELECT c.id, sc.title AS channel_title, sc.telegram_url AS channel_url,
                          c.tariff_name_snapshot, c.duration_seconds_snapshot,
                          c.price_kopecks_snapshot, c.currency_snapshot,
                          c.moderation_status, c.payment_status, c.placement_status,
                          c.requested_start_at, c.scheduled_start_at, c.scheduled_end_at
                   FROM campaigns AS c
                   JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                   WHERE c.id = %s AND c.advertiser_user_id = %s""",
                (campaign_id, user["id"]),
            ).fetchone()
            if campaign is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            observation = connection.execute(
                """SELECT COUNT(*) FILTER (WHERE o.result = 'visible') AS visible_count,
                          COUNT(*) FILTER (WHERE o.result = 'not_visible') AS not_visible_count,
                          COUNT(e.id) AS private_screenshot_count,
                          (SELECT newest.result FROM placement_observations AS newest
                           WHERE newest.campaign_id = %s ORDER BY newest.id DESC LIMIT 1)
                             AS latest_result,
                          (SELECT newest.observed_at FROM placement_observations AS newest
                           WHERE newest.campaign_id = %s ORDER BY newest.id DESC LIMIT 1)
                             AS latest_observed_at
                   FROM placement_observations AS o
                   LEFT JOIN placement_evidence AS e ON e.observation_id = o.id
                   WHERE o.campaign_id = %s""",
                (campaign_id, campaign_id, campaign_id),
            ).fetchone()
            timeline = connection.execute(
                """SELECT event_type, created_at
                   FROM audit_events
                   WHERE campaign_id = %s AND event_type = ANY(%s::text[])
                   ORDER BY created_at ASC, id ASC LIMIT 100""",
                (campaign_id, sorted(PUBLIC_EVENTS)),
            ).fetchall()
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return build_campaign_report(campaign, observation or {}, timeline)


@app.get("/api/v1/campaigns/{campaign_id}/report.csv", tags=["campaigns"])
def download_campaign_report_csv(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Приватная выгрузка: повторно используем проверку владельца JSON-отчёта."""
    report = get_campaign_report(campaign_id, user)
    body = render_campaign_csv(report)
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="mtproxy-pushok-report-' + str(campaign_id) + '.csv"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex",
        },
    )


@app.get("/api/v1/campaigns/{campaign_id}/display-observation", tags=["campaigns"])
def get_campaign_placement_observation(
    campaign_id: UUID,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Рекламодатель видит только публичное резюме последнего отчёта по своей заявке."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT c.id, obs.result, obs.observed_at, obs.recorded_at
                   FROM campaigns AS c
                   LEFT JOIN LATERAL (
                     SELECT result, observed_at, recorded_at
                     FROM placement_observations
                     WHERE campaign_id = c.id ORDER BY id DESC LIMIT 1
                   ) AS obs ON TRUE
                   WHERE c.id = %s AND c.advertiser_user_id = %s""",
                (campaign_id, user["id"]),
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    return {
        "campaign_id": campaign_id, "result": row["result"] or "not_reported",
        "observed_at": row["observed_at"], "recorded_at": row["recorded_at"],
        "operator_report_only": bool(row["result"]), "telegram_api_verified": False,
    }


@app.post("/api/v1/operator/campaigns/{campaign_id}/activate", response_model=CampaignResponse, tags=["operator"])
def activate_placement(
    campaign_id: UUID,
    operator: dict[str, Any] = Depends(require_operator),
) -> CampaignResponse:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE campaigns AS c
                SET placement_status = 'active', updated_at = now()
                WHERE c.id = %s AND c.placement_status = 'scheduled'
                  AND c.moderation_status = 'approved' AND c.payment_status = 'verified'
                  AND c.scheduled_start_at <= now() AND c.scheduled_end_at > now()
                  AND EXISTS (
                    SELECT 1 FROM placement_observations AS o
                    WHERE o.id = (
                      SELECT newest.id FROM placement_observations AS newest
                      WHERE newest.campaign_id = c.id ORDER BY newest.id DESC LIMIT 1
                    )
                      AND o.result = 'visible'
                      AND o.method = 'separate_unsubscribed_client'
                      AND o.observed_at >= c.scheduled_start_at
                      AND o.observed_at <= now()
                      AND o.recorded_at >= now() - interval '10 minutes'
                  )
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (campaign_id,),
            ).fetchone()
            if row is None:
                exists = connection.execute("SELECT 1 FROM campaigns WHERE id = %s", (campaign_id,)).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="campaign_not_found")
                raise HTTPException(status_code=409, detail="placement_not_in_active_window")
            connection.execute(
                "INSERT INTO audit_events (actor_user_id, campaign_id, event_type) VALUES (%s, %s, 'placement_activated_manually')",
                (operator["id"], campaign_id),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


@app.post("/api/v1/operator/campaigns/{campaign_id}/complete", response_model=CampaignResponse, tags=["operator"])
def complete_placement(
    campaign_id: UUID,
    payload: PlacementCompletionRequest,
    operator: dict[str, Any] = Depends(require_operator),
) -> CampaignResponse:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                UPDATE campaigns
                SET placement_status = 'completed', updated_at = now()
                WHERE id = %s AND placement_status = 'active'
                RETURNING id, channel_id, tariff_id, tariff_name_snapshot,
                          duration_seconds_snapshot, price_kopecks_snapshot,
                          currency_snapshot, moderation_status, payment_status,
                          placement_status, requested_start_at, scheduled_start_at,
                          scheduled_end_at, moderator_comment
                """,
                (campaign_id,),
            ).fetchone()
            if row is None:
                exists = connection.execute("SELECT 1 FROM campaigns WHERE id = %s", (campaign_id,)).fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail="campaign_not_found")
                raise HTTPException(status_code=409, detail="placement_not_active")
            connection.execute(
                """INSERT INTO audit_events (actor_user_id, campaign_id, event_type, details_json)
                   VALUES (%s, %s, 'placement_completed_manually',
                           jsonb_build_object('tag_removed_confirmed', true,
                                              'verification_method', %s::text))""",
                (operator["id"], campaign_id, payload.verification_method),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)


@app.get("/api/v1/operator/campaigns", response_model=list[CampaignResponse], tags=["operator"])
def list_operator_campaigns(
    moderation_status: str | None = None,
    payment_status: str | None = None,
    operator: dict[str, Any] = Depends(require_operator),
) -> list[CampaignResponse]:
    valid_moderation = {"draft", "pending", "approved", "rejected", "changes_requested"}
    valid_payment = {"not_required", "awaiting_payment", "proof_submitted", "verified", "rejected", "refunded"}
    if moderation_status is not None and moderation_status not in valid_moderation:
        raise HTTPException(status_code=422, detail="invalid_moderation_status")
    if payment_status is not None and payment_status not in valid_payment:
        raise HTTPException(status_code=422, detail="invalid_payment_status")
    filters = []
    params: list[str] = []
    if moderation_status is not None:
        filters.append("c.moderation_status = %s")
        params.append(moderation_status)
    if payment_status is not None:
        filters.append("c.payment_status = %s")
        params.append(payment_status)
    where_clause = " AND ".join(filters) if filters else "TRUE"
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute(
                f"""
                SELECT sc.title AS channel_title, sc.telegram_url AS channel_url,
                       c.id, c.channel_id, c.tariff_id, c.tariff_name_snapshot,
                       c.duration_seconds_snapshot, c.price_kopecks_snapshot,
                       c.currency_snapshot, c.moderation_status, c.payment_status,
                       c.placement_status, c.requested_start_at, c.scheduled_start_at,
                       c.scheduled_end_at, c.moderator_comment,
                       latest_proof.review_comment AS payment_review_comment
                FROM campaigns AS c
                JOIN sponsor_channels AS sc ON sc.id = c.channel_id
                LEFT JOIN LATERAL (
                    SELECT p.review_comment
                    FROM payment_proofs AS p
                    WHERE p.campaign_id = c.id AND p.reviewed_at IS NOT NULL
                    ORDER BY p.submitted_at DESC
                    LIMIT 1
                ) AS latest_proof ON TRUE
                WHERE {where_clause}
                ORDER BY c.created_at DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [_campaign_response(row) for row in rows]


@app.get("/api/v1/public-config", tags=["configuration"])
def public_config() -> dict[str, str]:
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    return {"telegram_bot_username": "" if username.startswith("REPLACE_WITH") else username}


@app.get("/api/v1/channels", response_model=list[ChannelResponse], tags=["channels"])
def list_channels(user: dict[str, Any] = Depends(get_current_user)) -> list[ChannelResponse]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id, telegram_url, title FROM sponsor_channels WHERE owner_user_id = %s ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [ChannelResponse(**row) for row in rows]


@app.get("/api/v1/operator/proxy-nodes", tags=["operator"])
def list_proxy_nodes(operator: dict[str, Any] = Depends(require_operator)) -> list[dict[str, Any]]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            return connection.execute(
                """SELECT id, name, host, port, status, target_engine,
                          engine_selection_mode, deployment_profile
                   FROM proxy_nodes ORDER BY name"""
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc


@app.get("/api/v1/operator/campaigns/{campaign_id}/payment-proofs", tags=["operator"])
def list_payment_proofs(
    campaign_id: UUID,
    operator: dict[str, Any] = Depends(require_operator),
) -> list[dict[str, Any]]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            return connection.execute(
                """
                SELECT id, original_file_name, content_type, size_bytes, submitted_at,
                       reviewed_at, review_comment
                FROM payment_proofs WHERE campaign_id = %s ORDER BY submitted_at DESC
                """,
                (campaign_id,),
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc



@app.get("/api/v1/admin/payments/status", tags=["admin"])
def read_payment_settings(
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Операционный обзор без ключей ЮKassa, токена Telegram и права включить оплату.

    Отчёт показывает лишь наличие цен и записи в собственной БД;
    это НЕ подтверждение работоспособности Telegram/эквайринга.
    """
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            counts = connection.execute(
                """SELECT
                  (SELECT count(*) FROM tariffs
                   WHERE is_active=TRUE AND stars_price_xtr IS NOT NULL)
                        AS priced_stars_tariffs,
                  (SELECT count(*) FROM stars_payment_receipts
                   WHERE handling_status='requires_review')
                        AS disputed_stars_receipts,
                  (SELECT count(*) FROM payment_attempts
                   WHERE provider='telegram_stars' AND status='succeeded')
                        AS recorded_stars_payments,
                  (SELECT count(*) FROM payment_attempts
                   WHERE provider='sbp' AND status='succeeded')
                        AS recorded_sbp_payments,
                  (SELECT count(*) FROM payment_attempts WHERE provider='sbp'
                   AND status='pending' AND sbp_requires_review) AS sbp_review,
                  (SELECT count(*) FROM payment_attempts WHERE provider='sbp'
                   AND status='pending' AND NOT sbp_requires_review) AS sbp_pending"""
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    safe = counts or {}
    return {
        "scope": "database_and_server_configuration_only",
        "stars": {
            "invoice_enabled_in_server_config": stars_invoices_enabled(),
            "priced_active_tariffs": int(safe.get("priced_stars_tariffs") or 0),
            "needs_manual_review": int(safe.get("disputed_stars_receipts") or 0),
            "recorded_successes": int(safe.get("recorded_stars_payments") or 0),
            "telegram_acceptance_verified": False,
            "refund_workflow_verified": False,
        },
        "sbp": {
            "integration": "external_web_checkout",
            "checkout_enabled": sbp_checkout.settings()['enabled'],
            "test_mode": sbp_checkout.settings()['test_mode'],
            "recorded_successes": int(safe.get("recorded_sbp_payments") or 0),
            "background_enabled": os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') == '1',
            "pending_checks": int(safe.get('sbp_pending') or 0),
            "needs_manual_review": int(safe.get('sbp_review') or 0),
            "provider_verified": False,
        },
        "operator_can_enable_payments_from_ui": False,
        "secrets_exposed": False,
    }


@app.get("/api/v1/admin/tariffs", response_model=list[TariffAdminResponse], tags=["admin"])
def list_admin_tariffs(
    admin: dict[str, Any] = Depends(require_admin),
) -> list[TariffAdminResponse]:
    """Действующие и отключённые тарифы; цены XTR видны только администратору."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            rows = connection.execute(
                """SELECT id, name, duration_seconds, price_kopecks, currency,
                          is_active, stars_price_xtr
                   FROM tariffs ORDER BY duration_seconds, id"""
            ).fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return [TariffAdminResponse(**{**row, "currency": row["currency"].strip()}) for row in rows]


@app.put("/api/v1/admin/tariffs/{tariff_id}", response_model=TariffAdminResponse, tags=["admin"])
def update_admin_tariff(
    tariff_id: UUID,
    payload: TariffSettingsUpdate,
    admin: dict[str, Any] = Depends(require_admin),
) -> TariffAdminResponse:
    """Зафиксировать новую публичную цену без пересчёта старых кампаний."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """UPDATE tariffs
                   SET name = %s, duration_seconds = %s, price_kopecks = %s,
                       stars_price_xtr = %s, is_active = %s
                   WHERE id = %s AND btrim(currency) = 'RUB'
                   RETURNING id, name, duration_seconds, price_kopecks,
                             currency, stars_price_xtr, is_active""",
                (payload.name, payload.duration_seconds, payload.price_kopecks,
                 payload.stars_price_xtr, payload.is_active, tariff_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="tariff_not_found")
            connection.execute(
                """INSERT INTO audit_events(actor_user_id, event_type, details_json)
                   VALUES (%s, 'tariff_settings_updated',
                           jsonb_build_object('tariff_id', %s::text,
                                              'price_kopecks', %s::int,
                                              'stars_price_xtr', %s::int,
                                              'duration_seconds', %s::int,
                                              'is_active', %s::boolean))""",
                (admin["id"], str(tariff_id), payload.price_kopecks,
                 payload.stars_price_xtr, payload.duration_seconds, payload.is_active),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return TariffAdminResponse(**{**row, "currency": row["currency"].strip()})


class EngineSelectionInput(BaseModel):
    deployment: Literal["any", "native", "docker"] = "any"
    environment: Literal["production", "staging"] = "production"
    engine_mode: Literal["auto", "manual"] = "auto"
    preferred_engine: Literal["official_mtproxy", "official_docker", "telemt"] | None = None


class ProxyNodeCreate(EngineSelectionInput):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)


def _engine_plan(payload: EngineSelectionInput) -> dict[str, Any]:
    try:
        return recommend_engine(**payload.model_dump(include={
            "deployment", "environment", "engine_mode", "preferred_engine"
        }))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/admin/launch-readiness", tags=["admin"])
def read_launch_readiness(
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Только агрегаты БД: не сканировать сеть, не читать рекламные теги и секреты."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            counts = connection.execute(
                """SELECT
                  (SELECT count(*) FROM proxy_nodes) AS nodes_total,
                  (SELECT count(*) FROM proxy_nodes WHERE status = 'active') AS nodes_active,
                  (SELECT count(*) FROM proxy_sponsor_registrations AS r
                   JOIN proxy_nodes AS n ON n.id = r.proxy_node_id
                   WHERE n.status = 'active') AS nodes_with_registered_tag,
                  (SELECT count(*) FROM campaigns) AS campaigns_total,
                  (SELECT count(*) FROM campaigns WHERE moderation_status = 'pending') AS moderation_pending,
                  (SELECT count(*) FROM campaigns WHERE payment_status = 'proof_submitted') AS payments_pending,
                  (SELECT count(*) FROM campaigns WHERE payment_status = 'verified') AS payments_verified,
                  (SELECT count(*) FROM campaigns WHERE placement_status = 'scheduled') AS placements_scheduled,
                  (SELECT count(*) FROM campaigns WHERE placement_status = 'active') AS placements_active,
                  (SELECT count(*) FROM campaigns WHERE placement_status = 'completed') AS placements_completed,
                  (SELECT count(*) FROM placement_observations) AS manual_observations
                """
            ).fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return build_launch_readiness(counts or {})


@app.get("/api/v1/admin/pushok-core/readiness", tags=["admin"])
def read_pushok_core_readiness(
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Only describes source/build capabilities. Never executes the proxy on API host."""
    return core_readiness()


@app.get("/api/v1/admin/pushok-core/diagnostic-plan", tags=["admin"])
def read_pushok_core_diagnostic_plan(
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Read-only checklist; never executes Docker, SSH or proxy actions."""
    return core_diagnostic_plan()


@app.get("/api/v1/admin/proxy-engines", tags=["admin"])
def list_proxy_engines(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"adapters": engine_catalog(), "changes_applied": False}


@app.post("/api/v1/admin/proxy-engines/recommend", tags=["admin"])
def recommend_proxy_engine(
    payload: EngineSelectionInput,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return _engine_plan(payload)


class ProxyNodeStatus(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


def normalize_proxy_host(host: str) -> str:
    host = host.strip().lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise HTTPException(status_code=422, detail="invalid_proxy_host") from None
        return host


@app.post("/api/v1/admin/proxy-nodes", status_code=201, tags=["admin"])
def create_proxy_node(
    payload: ProxyNodeCreate,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="proxy_name_required")
    host = normalize_proxy_host(payload.host)
    plan = _engine_plan(payload)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                INSERT INTO proxy_nodes (name, host, port, target_engine,
                                         engine_selection_mode, deployment_profile)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, name, host, port, status, target_engine,
                          engine_selection_mode, deployment_profile
                """,
                (name, host, payload.port, plan["engine"],
                 plan["engine_mode"], plan["deployment"]),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, event_type, details_json)
                VALUES (%s, 'proxy_node_created', jsonb_build_object('node_id', %s::text))
                """,
                (admin["id"], str(row["id"])),
            )
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="proxy_node_already_exists") from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {**row, "provisioning_verified": False, "changes_applied": False}


@app.patch("/api/v1/admin/proxy-nodes/{node_id}/engine-plan", tags=["admin"])
def update_proxy_engine_plan(
    node_id: UUID,
    payload: EngineSelectionInput,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Менять только запись о целевом движке. Не переключать MTProxy на сервере."""
    plan = _engine_plan(payload)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            node = connection.execute(
                "SELECT id FROM proxy_nodes WHERE id = %s FOR UPDATE",
                (node_id,),
            ).fetchone()
            if node is None:
                raise HTTPException(status_code=404, detail="proxy_node_not_found")
            occupied = connection.execute(
                """SELECT 1 FROM campaigns WHERE proxy_node_id = %s
                   AND placement_status IN ('scheduled', 'active') LIMIT 1""",
                (node_id,),
            ).fetchone()
            if occupied:
                raise HTTPException(status_code=409, detail="proxy_node_has_placements")
            sponsor = connection.execute(
                "SELECT engine FROM proxy_sponsor_registrations WHERE proxy_node_id = %s",
                (node_id,),
            ).fetchone()
            if sponsor and sponsor["engine"] != plan["engine"]:
                raise HTTPException(status_code=409, detail="sponsor_engine_mismatch")
            row = connection.execute(
                """UPDATE proxy_nodes
                   SET target_engine = %s, engine_selection_mode = %s,
                       deployment_profile = %s, updated_at = now()
                   WHERE id = %s
                   RETURNING id, name, host, port, status, target_engine,
                             engine_selection_mode, deployment_profile""",
                (plan["engine"], plan["engine_mode"], plan["deployment"], node_id),
            ).fetchone()
            connection.execute(
                """INSERT INTO audit_events(actor_user_id, event_type, details_json)
                   VALUES (%s, 'proxy_engine_plan_updated',
                           jsonb_build_object('proxy_node_id', %s::text,
                                              'target_engine', %s::text,
                                              'engine_mode', %s::text))""",
                (admin["id"], str(node_id), plan["engine"], plan["engine_mode"]),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return {**row, "provisioning_verified": False, "changes_applied": False}


@app.patch("/api/v1/admin/proxy-nodes/{node_id}/status", tags=["admin"])
def change_proxy_node_status(
    node_id: UUID,
    payload: ProxyNodeStatus,
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            node = connection.execute(
                "SELECT id FROM proxy_nodes WHERE id = %s FOR UPDATE", (node_id,)
            ).fetchone()
            if node is None:
                raise HTTPException(status_code=404, detail="proxy_node_not_found")
            if payload.status == "paused":
                occupied = connection.execute(
                    """
                    SELECT 1 FROM campaigns
                    WHERE proxy_node_id = %s AND placement_status IN ('scheduled', 'active')
                    LIMIT 1
                    """, (node_id,),
                ).fetchone()
                if occupied:
                    raise HTTPException(status_code=409, detail="proxy_node_has_placements")
            row = connection.execute(
                """
                UPDATE proxy_nodes SET status = %s, updated_at = now()
                WHERE id = %s RETURNING id, name, host, port, status
                """, (payload.status, node_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO audit_events (actor_user_id, event_type, details_json)
                VALUES (%s, 'proxy_node_status_changed',
                        jsonb_build_object('node_id', %s::text, 'status', %s::text))
                """, (admin["id"], str(node_id), payload.status),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return row




@app.patch("/api/v1/campaigns/{campaign_id}/channel", response_model=ChannelResponse, tags=["campaigns"])
def correct_campaign_channel(
    campaign_id: UUID,
    payload: ChannelCreate,
    user: dict[str, Any] = Depends(get_current_user),
) -> ChannelResponse:
    title = payload.title.strip()
    try:
        parsed = urlparse(payload.telegram_url)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_telegram_channel_url") from None
    if not title:
        raise HTTPException(status_code=422, detail="channel_title_required")
    if (parsed.scheme != "https" or parsed.hostname not in {"t.me", "telegram.me"}
        or not parsed.path.strip("/") or parsed.query or parsed.fragment
        or parsed.netloc.lower() != parsed.hostname):
        raise HTTPException(status_code=422, detail="invalid_telegram_channel_url")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row) as connection:
            channel = connection.execute(
                """
                SELECT ch.id FROM sponsor_channels AS ch
                JOIN campaigns AS c ON c.channel_id = ch.id
                WHERE c.id = %s AND c.advertiser_user_id = %s
                  AND ch.owner_user_id = %s
                FOR UPDATE OF ch
                """, (campaign_id, user["id"], user["id"]),
            ).fetchone()
            if channel is None:
                raise HTTPException(status_code=404, detail="campaign_not_found")
            blocked = connection.execute(
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
        raise HTTPException(503, 'database_unavailable') from exc


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
                "SELECT duration_seconds FROM tariffs WHERE id = %s AND is_active = TRUE",
                (tariff_id,),
            ).fetchone()
            if tariff is None:
                raise HTTPException(status_code=404, detail="active_tariff_not_found")
            duration = timedelta(seconds=tariff["duration_seconds"])
            ends = [dt.astimezone(timezone.utc) + duration for dt in valid]
            rows = connection.execute(
                """
                SELECT w.starts_at, count(n.id) AS available_nodes
                FROM unnest(%s::timestamptz[], %s::timestamptz[]) AS w(starts_at, ends_at)
                LEFT JOIN proxy_nodes AS n ON n.status = 'active'
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
             "status": slot_status(dt, now, int(counts.get(dt, 0)) if dt in valid else 0)}
            for dt in starts
        ],
    }


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/", include_in_schema=False)
def cabinet() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


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
                "SELECT id, target_engine FROM proxy_nodes WHERE id = %s FOR UPDATE",
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
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    return build_readiness(row)
