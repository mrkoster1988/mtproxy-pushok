"""Сводка данных о готовности MTProxy Pushok, без ложных live-проверок.

Только инвентаризация базы; CI, VPS, Telegram-клиент и реальные показы
не проверяются этим модулем и никогда не выставляются в true.
"""

def build_launch_readiness(counts: dict) -> dict:
    keys = (
        "nodes_total", "nodes_active", "nodes_with_registered_tag",
        "campaigns_total", "moderation_pending", "payments_pending",
        "payments_verified", "placements_scheduled", "placements_active",
        "placements_completed", "manual_observations",
    )
    safe = {key: max(0, int(counts.get(key) or 0)) for key in keys}
    checks = [
        {
            "id": "nodes_in_database",
            "title": "Прокси-узлы внесены в реестр",
            "status": "data_present" if safe["nodes_total"] else "missing",
            "count": safe["nodes_total"],
            "proof": "database_inventory_only",
        },
        {
            "id": "registered_sponsor_tags",
            "title": "Спонсорские теги зарегистрированы для активных узлов",
            "status": "data_present" if safe["nodes_with_registered_tag"] else "missing",
            "count": safe["nodes_with_registered_tag"],
            "proof": "registered_not_applied",
        },
        {
            "id": "campaigns_in_database",
            "title": "Рекламные заявки сохранены",
            "status": "data_present" if safe["campaigns_total"] else "missing",
            "count": safe["campaigns_total"],
            "proof": "database_inventory_only",
        },
        {
            "id": "ci_acceptance",
            "title": "Полный CI на коммите версии",
            "status": "not_verified",
            "proof": "external_ci_required",
        },
        {
            "id": "real_mtproxy",
            "title": "Реальный MTProxy подключается с клиента Telegram",
            "status": "not_verified",
            "proof": "separate_vps_or_staging_required",
        },
        {
            "id": "real_sponsor",
            "title": "Спонсорский канал действительно показан и снят",
            "status": "not_verified",
            "proof": "independent_telegram_client_required",
        },
        {
            "id": "payments",
            "title": "Реальные платежи, возвраты и сверка",
            "status": "not_verified",
            "proof": "payment_provider_or_manual_reconciliation_required",
        },
        {
            "id": "installer",
            "title": "Установка и восстановление на Ubuntu 24.04",
            "status": "not_verified",
            "proof": "separate_vps_required",
        },
    ]
    return {
        "scope": "read_only_database_inventory",
        "statistics": safe,
        "checks": checks,
        "ready_for_public_launch": False,
        "real_telegram_verified": False,
        "real_sponsor_verified": False,
        "ci_verified": False,
        "vps_verified": False,
        "payments_automatically_verified": False,
        "changes_applied": False,
        "recommended_next_steps": [
            "Подтвердить успешный GitHub Actions на SHA версии, включая временную PostgreSQL",
            "Проверить MTProxy, спонсора и снятие тега в отдельном Telegram-клиенте",
            "Проверить заказ, модерацию, оплату, слот и отчёт на staging",
            "Проверить установщик, секреты, бэкапы и восстановление на VPS",
        ],
    }
