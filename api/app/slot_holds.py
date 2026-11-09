"""Бизнес-правила временных удержаний спонсорских слотов.

Это не подтверждённая оплата и не запуск Telegram promotion.
"""
from datetime import datetime, timedelta, timezone

HOLD_TTL = timedelta(minutes=15)


def normalize_hold_window(start: datetime, duration_seconds: int, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("slot_start_requires_timezone")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("current_time_requires_timezone")
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        raise ValueError("invalid_duration")
    start_utc = start.astimezone(timezone.utc)
    if start_utc <= now.astimezone(timezone.utc):
        raise ValueError("slot_start_must_be_future")
    return start_utc, start_utc + timedelta(seconds=duration_seconds), now + HOLD_TTL
