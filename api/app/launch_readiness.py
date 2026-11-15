"""Приёмка по сохранённым результатам. Не выполняет live-проверки VPS/Telegram."""
from datetime import datetime, timedelta, timezone

MANUAL_CHECKS = {
    "backup_restore": ("Восстановление резервной копии", "База и файлы восстановлены в отдельное окружение; записи, файлы и вход проверены."),
    "stars_payment": ("Stars: оплата, сверка и возврат", "Реальная оплата отражена один раз; сумма сверена; возврат получен и отражён в кабинете."),
    "sbp_payment": ("СБП: оплата, сверка и возврат", "Реальная оплата отражена один раз; сумма сверена с провайдером; возврат получен и отражён в кабинете."),
}
MAX_AGE = timedelta(days=7)


def timestamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def build_launch_readiness(counts, campaign=None, manual_checks=(), *, version=None, commit=None, ci=None, now=None):
    now = now or datetime.now(timezone.utc)
    keys = ("nodes_total", "nodes_active", "nodes_with_registered_tag", "campaigns_total", "moderation_pending",
            "payments_pending", "payments_verified", "placements_scheduled", "placements_active",
            "placements_completed", "manual_observations")
    safe = {key: max(0, int(counts.get(key) or 0)) for key in keys}
    checks = []

    def add(key, title, status="not_verified", **extra):
        item = dict(id=key, title=title, status=status, **extra)
        checks.append(item)
        return item

    def valid_time(value):
        at = timestamp(value)
        return at is not None and at <= now

    for key, title, count in (
        ("nodes_in_database", "Активные узлы в реестре", safe["nodes_active"]),
        ("registered_sponsor_tags", "Спонсорские теги активных узлов", safe["nodes_with_registered_tag"]),
        ("campaigns_in_database", "Рекламные заявки", safe["campaigns_total"]),
    ):
        add(key, title, "data_present" if count else "missing", count=count, proof="database_inventory_only")
    ci_ok = bool(ci and ci.get("verified") is True and commit and ci.get("commit") == commit)
    add("ci_acceptance", "Проверки установленной версии", "verified" if ci_ok else "not_verified",
        proof="github_actions_installed_commit", commit=commit, checked_at=ci.get("checked_at") if ci else None,
        action="Профиль → версия и обновления: установите проверенный выпуск и обновите сводку.")

    c = campaign or {}
    observed = timestamp(c.get("observed_at"))
    start, end = timestamp(c.get("scheduled_start_at")), timestamp(c.get("scheduled_end_at"))
    valid_observation = bool(observed and start and end and start <= observed < end
                             and c.get("method") == "separate_unsubscribed_client")
    known = valid_observation and valid_time(observed)
    visible = known and c.get("result") == "visible"
    status = "reported" if visible else "failed" if known and c.get("result") == "not_visible" else "stale" if valid_observation else "not_verified"
    details = dict(campaign_id=c.get("campaign_id"), node_name=c.get("node_name"), observed_at=c.get("observed_at"),
                   observation_id=c.get("observation_id"), proof="operator_telegram_observation",
                   action="Профиль → Модерация → Управлять размещением: сохраните фактический результат проверки показа.")
    add("real_mtproxy", "Подключение через MTProxy при проверке показа", "reported" if visible else "stale" if valid_observation and not known else "not_verified", **details)
    add("real_sponsor", "Спонсорский канал виден в Telegram", status, **details)
    activated, completed = timestamp(c.get("activated_at")), timestamp(c.get("completed_at"))
    cycle = bool(visible and activated and completed and c.get("placement_status") == "completed"
                 and observed <= activated <= completed <= now
                 and c.get("tag_removed_confirmed") is True
                 and c.get("verification_method") == "separate_unsubscribed_client")
    add("campaign_cycle", "Полная кампания: показ и завершение", "reported" if cycle else "not_verified",
        proof="operator_campaign_completion", campaign_id=c.get("campaign_id"), observed_at=c.get("completed_at") if cycle else None,
        action="В карточке активного размещения после окончания проверьте прекращение показа и подтвердите завершение. Возврат того же тега не подтверждает снятие спонсора.")

    latest = {}
    for row in manual_checks:  # SQL возвращает последнюю запись каждого вида.
        details = row.get("details_json") or {}
        key = details.get("check_id")
        if key in MANUAL_CHECKS and key not in latest:
            latest[key] = (row, details)
    for key, (title, instructions) in MANUAL_CHECKS.items():
        row, data = latest.get(key, ({}, {}))
        valid_report = bool(data and isinstance(data.get("version"), str) and data.get("version") and valid_time(data.get("checked_at")))
        passed = valid_report and data.get("result") == "passed" and data.get("steps_confirmed") is True and data.get("performed_on_vps") is True
        state = "reported" if passed else "failed" if valid_report and data.get("result") == "failed" else "stale" if data else "not_verified"
        add(key, title, state, proof="administrator_acceptance_report", observed_at=data.get("checked_at"),
            recorded_at=row.get("created_at"), report_id=row.get("id"), version=data.get("version"),
            note=data.get("note"), action=instructions)
    by_id = {item["id"]: item for item in checks}
    free_ids = ("ci_acceptance", "real_mtproxy", "real_sponsor", "campaign_cycle", "backup_restore")
    paid_ids = (*free_ids, "stars_payment", "sbp_payment")
    def pending(ids):
        return [key for key in ids if by_id[key]["status"] not in ("verified", "reported")]
    free_pending, paid_pending = pending(free_ids), pending(paid_ids)
    return {
        "scope": "recorded_acceptance_not_live_monitoring", "version": version, "checked_at": now,
        "statistics": safe, "checks": checks, "release_ci": ci,
        "ready_for_free_pilot": not free_pending, "ready_for_public_launch": not paid_pending,
        "pending_free": free_pending, "pending_paid": paid_pending,
        "free_total": len(free_ids), "paid_total": len(paid_ids),
        "real_telegram_verified": bool(visible), "real_sponsor_verified": bool(visible),
        "ci_verified": ci_ok, "vps_verified": by_id["backup_restore"]["status"] == "reported",
        "payments_automatically_verified": False, "telegram_api_verified": False, "changes_applied": False,
        "manual_check_types": [{"id": key, "title": title, "instructions": instructions} for key, (title, instructions) in MANUAL_CHECKS.items()],
        "note": "История приёмки по последней проверенной кампании на активном узле и отчётам администратора. Это не мониторинг всех узлов. Даты и версии указаны у результатов; обновление кабинета не стирает историю. При изменении узла, провайдера или процедуры восстановления повторите соответствующее испытание. Публикация и приём платежей сами не включаются.",
    }
