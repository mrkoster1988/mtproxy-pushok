import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

app = FastAPI(
    title="MTProxy Pushok API",
    version="0.004",
    description="API кабинета рекламодателя MTProxy Pushok.",
)

DATABASE_URL = os.environ["DATABASE_URL"]
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


class ModerationRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    comment: str | None = Field(default=None, max_length=2000)


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
                SELECT id, channel_id, tariff_id, tariff_name_snapshot,
                       duration_seconds_snapshot, price_kopecks_snapshot,
                       currency_snapshot, moderation_status, payment_status,
                       placement_status, requested_start_at, scheduled_start_at,
                       scheduled_end_at, moderator_comment
                FROM campaigns
                WHERE advertiser_user_id = %s
                ORDER BY created_at DESC
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
            row = connection.execute(
                """
                UPDATE campaigns
                SET moderation_status = 'pending', updated_at = now()
                WHERE id = %s AND advertiser_user_id = %s AND moderation_status = 'draft'
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
                raise HTTPException(status_code=409, detail="campaign_not_draft")
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
                VALUES (%s, %s, 'campaign_moderated', jsonb_build_object('decision', %s))
                """,
                (operator["id"], campaign_id, payload.decision),
            )
    except HTTPException:
        raise
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    return _campaign_response(row)
