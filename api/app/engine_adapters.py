"""Pushok Unified Engine: описание адаптеров и только планирование, без запуска."""
from dataclasses import dataclass
from typing import Literal

EngineId = Literal["official_mtproxy", "official_docker", "telemt"]


@dataclass(frozen=True)
class EngineAdapter:
    id: EngineId
    title: str
    deployment: str
    sponsor_format: str
    availability: str
    production_eligible: bool
    automatic_eligible: bool
    notes: str

    def describe(self) -> dict:
        return {
            "id": self.id, "title": self.title, "deployment": self.deployment,
            "sponsor_format": self.sponsor_format, "availability": self.availability,
            "production_eligible": self.production_eligible,
            "automatic_eligible": self.automatic_eligible, "notes": self.notes,
        }


ADAPTERS = (
    EngineAdapter(
        "official_mtproxy", "MTProxy Classic", "native", "-P",
        "installer_available_unverified", True, True,
        "Официальный исходный код Telegram. Нужна проверка реальной установки.",
    ),
    EngineAdapter(
        "official_docker", "MTProxy Docker", "docker", "TAG",
        "experimental_image", False, True,
        "Контейнерный запуск того же MTProxy; пока только тестовый стенд.",
    ),
    EngineAdapter(
        "telemt", "Telemt", "native", "ad_tag",
        "adapter_only", False, False,
        "Есть образец рекламной конфигурации; установщик ещё не готов.",
    ),
)
BY_ID = {adapter.id: adapter for adapter in ADAPTERS}


def get_adapter(engine: str) -> EngineAdapter:
    try:
        return BY_ID[engine]
    except (KeyError, TypeError) as exc:
        raise ValueError("unknown_proxy_engine") from exc


def engine_catalog() -> list[dict]:
    return [adapter.describe() for adapter in ADAPTERS]


def recommend_engine(
    *, deployment: str = "any", environment: str = "production",
    engine_mode: str = "auto", preferred_engine: str | None = None,
) -> dict:
    """Детерминированно выбрать ПЛАН, не устанавливая и не переключая движки."""
    if deployment not in ("any", "native", "docker"):
        raise ValueError("invalid_engine_deployment")
    if environment not in ("production", "staging"):
        raise ValueError("invalid_engine_environment")
    if engine_mode not in ("auto", "manual"):
        raise ValueError("invalid_engine_mode")
    if engine_mode == "auto":
        if preferred_engine is not None:
            raise ValueError("preferred_engine_for_manual_only")
        candidates = [
            adapter for adapter in ADAPTERS
            if adapter.automatic_eligible
            and (deployment == "any" or adapter.deployment == deployment)
            and (environment == "staging" or adapter.production_eligible)
        ]
        if not candidates:
            raise ValueError("no_verified_engine_for_profile")
        adapter = candidates[0]
        reason = "automatic_policy_no_runtime_probe"
    else:
        if preferred_engine is None:
            raise ValueError("manual_engine_required")
        adapter = get_adapter(preferred_engine)
        if deployment != "any" and deployment != adapter.deployment:
            raise ValueError("engine_deployment_mismatch")
        if environment == "production" and not adapter.production_eligible:
            raise ValueError("engine_not_approved_for_production")
        reason = "manual_selection_not_runtime_verification"
    return {
        "engine": adapter.id, "title": adapter.title, "engine_mode": engine_mode,
        "environment": environment, "deployment": adapter.deployment,
        "availability": adapter.availability, "reason": reason,
        "provisioning_verified": False, "changes_applied": False,
    }
