"""Проверка готовности спонсорского канала, без изменения MTProxy.

Регистрация proxy tag и выбор Set promotion в @MTProxyBot — разные действия.
Эта функция строит только проверочный план для живого оператора.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .engine_adapters import get_adapter

_TAG = re.compile(r"^[0-9a-f]{32}$")
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def normalize_ad_tag(tag: str) -> str:
    if not isinstance(tag, str):
        raise ValueError("Нужен строковый тег")
    result = tag.strip().lower()
    if not _TAG.fullmatch(result) or result == "0" * 32:
        raise ValueError("Ожидается настоящий 32-символьный hex-тег из @MTProxyBot")
    return result


def parse_public_channel(url: str) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    username = parts.path.strip("/")
    if (
        parts.scheme != "https" or parts.hostname not in ("t.me", "telegram.me")
        or parts.query or parts.fragment or parts.username or parts.password
        or port is not None or not _USERNAME.fullmatch(username)
    ):
        return None
    if username.lower() in {"joinchat", "addstickers", "share", "proxy", "socks", "login"}:
        return None
    return username


def build_readiness(row: dict, now: datetime | None = None) -> dict:
    """Проверить кампанию по фактам из БД. Не заявлять об изменении Telegram.

    Даже если все проверки пройдены, Set promotion нужно выполнить вручную
    в @MTProxyBot и подтвердить наблюдение с другого Telegram-аккаунта.
    """
    now = now or datetime.now(timezone.utc)
    start = row.get("scheduled_start_at")
    end = row.get("scheduled_end_at")
    # Naive timestamps or malformed tags must never turn a placement green.
    def aware(value):
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None

    try:
        tag_valid = normalize_ad_tag(row.get("ad_tag")) == row.get("ad_tag", "").strip().lower()
    except (ValueError, AttributeError):
        tag_valid = False
    time_valid = aware(start) and aware(end) and aware(now)
    checks = {
        "moderation_approved": row["moderation_status"] == "approved",
        "payment_verified": row["payment_status"] == "verified",
        "placement_scheduled": row["placement_status"] == "scheduled",
        "node_active": row["node_status"] == "active",
        "channel_verified": (
            row.get("telegram_channel_id") is not None
            and parse_public_channel(row["telegram_url"]) is not None
        ),
        "tag_registered": tag_valid,
        "window_order_valid": bool(time_valid and start < end),
        "window_not_expired": bool(time_valid and end > now),
    }
    engine = row.get("engine")
    return {
        "campaign_id": row["campaign_id"],
        "proxy_node_id": row.get("proxy_node_id"),
        "proxy_host": row.get("proxy_host"),
        "proxy_port": row.get("proxy_port"),
        "channel_url": row["telegram_url"],
        "scheduled_start_at": row.get("scheduled_start_at"),
        "scheduled_end_at": row.get("scheduled_end_at"),
        "engine": engine,
        "tag_ending": row["ad_tag"][-4:] if row.get("ad_tag") else None,
        "checks": checks,
        "ready_for_manual_preparation": all(checks.values()),
        "manual_steps": [
            "Проверьте реальную доступность прокси и резервную копию его конфигурации.",
            "Убедитесь, что ad_tag применён в конфигурации соответствующего прокси.",
            "Через @MTProxyBot → /myproxies → Set promotion вручную выберите публичный канал.",
            "Подтвердите показ с отдельного Telegram-клиента, не подписанного на канал.",
            "Фиксируйте активацию в кабинете только после фактического подтверждения.",
        ],
        "changes_applied": False,
    }


def render_config_fragment(engine: str, tag: str) -> str:
    """Сформировать ТОЛЬКО текст для ручного переноса, не команду исполнения."""
    tag = normalize_ad_tag(tag)
    style = get_adapter(engine).sponsor_format
    if style == "ad_tag":
        return '[general]\nuse_middle_proxy = true\nad_tag = "' + tag + '"'
    if style == "-P":
        return "-P " + tag
    if style == "TAG":
        return "TAG=" + tag
    raise ValueError("Неизвестный движок MTProxy")
