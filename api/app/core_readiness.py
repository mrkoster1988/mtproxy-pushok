"""Read-only, honest capability report for an undeployed Pushok Source Core.

API containers intentionally do not read the developer's .build directory or start a
proxy. No probe here is allowed to claim real TCP reachability or sponsor delivery.
"""
from typing import Any

UPSTREAM_COMMIT = "66708d2866f3f3a4541c4fa67984d408e71a8af4"
PATCH_ID = "cli-build-info-001"
CORE_VERSION = "0.027"


def core_readiness() -> dict[str, Any]:
    return {
        "product": "Pushok Source Core",
        "project_version": "0.058",
        "core_version": CORE_VERSION,
        "upstream_commit": UPSTREAM_COMMIT,
        "patch": PATCH_ID,
        "phase": "source_build_and_offline_smoke",
        "source_pinned": True,
        "local_build_verified": None,
        "runtime_status": "not_checked",
        "telegram_connection_verified": False,
        "sponsor_verified": False,
        "live_proxy_healthy": None,
        "changes_applied": False,
        "notes": "API не выполняет команды на сервере. Локальная проверка: python3 scripts/core_stage.py --inspect. "
                 "Фактический запуск MTProxy, доступность Telegram и спонсор требуют отдельных испытаний.",
    }

def core_diagnostic_plan() -> dict[str, Any]:
    """Static admin view of what CI may exercise; never query hosts or run a proxy."""
    return {
        "project_version": "0.058",
        "execution": "not_started_by_api",
        "ci_probe_scope": "offline_127.0.0.1_in_docker_network_none",
        "steps": [
            {"id": "binary", "title": "Проверка версии, SHA256 и лицензии",
             "scope": "offline_artifact", "verification": "ci_only"},
            {"id": "tcp_accept", "title": "Локальное TCP-подключение в контейнере",
             "scope": "isolated_container", "verification": "ci_only"},
            {"id": "invalid_header", "title": "Некорректный 64-байтный заголовок",
             "scope": "isolated_container", "verification": "ci_only"},
            {"id": "reconnect", "title": "Повторное TCP-подключение после ошибки",
             "scope": "isolated_container", "verification": "ci_only"},
            {"id": "stop", "title": "Остановка и удаление контейнера",
             "scope": "isolated_container", "verification": "ci_only"},
            {"id": "valid_handshake", "title": "Настоящее MTProxy-рукопожатие",
             "scope": "requires_separate_staging", "verification": "not_verified"},
            {"id": "telegram_client", "title": "Подключение клиента Telegram",
             "scope": "requires_separate_staging", "verification": "not_verified"},
            {"id": "sponsor", "title": "Фактический показ спонсорского канала",
             "scope": "requires_separate_staging", "verification": "not_verified"},
            {"id": "service_rollback", "title": "Возврат реально работающей службы",
             "scope": "not_implemented", "verification": "not_verified"},
        ],
        "current_host_checked": False,
        "real_telegram_verified": False,
        "sponsor_verified": False,
        "live_service_changed": False,
    }
