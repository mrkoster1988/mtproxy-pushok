"""Оценка видимости слота в календаре (только чтение).

Календарь носит справочный характер: окончательную защиту от гонок
бронирования обеспечивает PostgreSQL EXCLUDE constraint.
"""
from datetime import datetime
from zoneinfo import ZoneInfo


def slot_status(start_at: datetime, now: datetime, available_nodes: int) -> str:
    """Вернуть причину доступности без записи и резервирования."""
    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timezone_required")
    if start_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("timezone_required")
    if not isinstance(available_nodes, int) or available_nodes < 0:
        raise ValueError("invalid_available_nodes")
    zone = start_at.tzinfo
    if isinstance(zone, ZoneInfo):
        naive = start_at.replace(tzinfo=None)
        roundtrip = start_at.astimezone(__import__("datetime").timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) != naive:
            return "invalid_local_time"
        # Не предлагаем неоднозначные часы: fold=0 и fold=1 соответствуют разным UTC.
        if start_at.replace(fold=0).utcoffset() != start_at.replace(fold=1).utcoffset():
            return "ambiguous_local_time"
    if start_at <= now:
        return "past"
    return "available" if available_nodes else "occupied"
