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
        "project_version": "0.029",
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
