"""Валидация человеческого наблюдения спонсора.

Запись оператора — декларация о визуальной проверке, НЕ доказательство от API Telegram.
Никаких обращений к Telegram и изменений MTProxy этот модуль не выполняет.
"""
from datetime import datetime, timedelta


def validate_observation_window(
    start: datetime | None,
    end: datetime | None,
    observed: datetime,
    now: datetime,
) -> None:
    for value in (start, end, observed, now):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("observation_timezone_required")
    if not start < end or not start <= observed < end:
        raise ValueError("observation_outside_placement_window")
    if observed < now - timedelta(minutes=10) or observed > now + timedelta(seconds=60):
        raise ValueError("observation_not_recent")
