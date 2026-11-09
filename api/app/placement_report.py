"""Публичный отчёт рекламодателя, только проверенные данные из БД.

Отчёты оператора не равны независимому подтверждению Telegram.
Не передаём заметки, контакты операторов, содержимое чеков и скриншотов.
"""
from datetime import datetime, timezone
from typing import Any

PUBLIC_EVENTS = frozenset({
    "campaign_submitted",
    "campaign_moderated",
    "payment_proof_submitted",
    "payment_reviewed",
    "placement_scheduled",
    "placement_activated_manually",
    "placement_completed_manually",
})

def build_campaign_report(
    campaign: dict[str, Any],
    observation: dict[str, Any],
    timeline: list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Превратить записи БД в безопасный summary без служебных деталей."""
    latest = observation.get("latest_result")
    if latest not in ("visible", "not_visible"):
        latest = None
    visible = max(0, int(observation.get("visible_count") or 0))
    missing = max(0, int(observation.get("not_visible_count") or 0))
    screenshots = max(0, int(observation.get("private_screenshot_count") or 0))
    events = [
        {"event": row["event_type"], "at": row["created_at"]}
        for row in timeline
        if row.get("event_type") in PUBLIC_EVENTS
    ]
    return {
        "campaign_id": campaign["id"],
        "channel": {
            "title": campaign["channel_title"],
            "telegram_url": campaign["channel_url"],
        },
        "tariff": {
            "name": campaign["tariff_name_snapshot"],
            "price_kopecks": campaign["price_kopecks_snapshot"],
            "currency": campaign["currency_snapshot"].strip(),
            "duration_seconds": campaign["duration_seconds_snapshot"],
        },
        "moderation_status": campaign["moderation_status"],
        "payment_status": campaign["payment_status"],
        "placement_status": campaign["placement_status"],
        "requested_start_at": campaign["requested_start_at"],
        "scheduled_start_at": campaign["scheduled_start_at"],
        "scheduled_end_at": campaign["scheduled_end_at"],
        "operator_observations": {
            "visible": visible,
            "not_visible": missing,
            "total": visible + missing,
            "latest_result": latest or "not_reported",
            "latest_observed_at": observation.get("latest_observed_at") if latest else None,
            "private_screenshot_count": screenshots,
            "private_screenshots_downloadable": False,
        },
        "timeline": events,
        "verification": {
            "source": "manual_operator_observations",
            "status": ("operator_reported_visible" if latest == "visible"
                       else "operator_reported_not_visible" if latest == "not_visible"
                       else "no_operator_report"),
            "telegram_api_verified": False,
            "impressions_verified": False,
            "impressions_count": None,
        },
        "generated_at": generated_at or datetime.now(timezone.utc),
    }
