"""Conservative production gates for Pushok Core.

Evidence is supplied explicitly by a separate operator/test runner. No host probes,
network requests or side effects are performed by this module.
"""
from dataclasses import dataclass
from typing import Mapping

GATES = (
    ("artifact", "Проверены SHA256, источник и лицензия"),
    ("isolated_tcp", "Изолированный TCP smoke прошёл"),
    ("valid_handshake", "MTProxy handshake подтверждён"),
    ("telegram_client", "Клиент Telegram подключился к тестовому узлу"),
    ("sponsor_delivery", "Спонсорский канал подтверждён на клиенте"),
    ("rollback", "Откат живой службы проверен"),
)


@dataclass(frozen=True)
class GateResult:
    name: str
    description: str
    passed: bool


def evaluate_release_gates(evidence: Mapping[str, bool]) -> dict:
    """Fail closed: missing, false and non-boolean evidence never passes."""
    unknown = set(evidence) - {name for name, _ in GATES}
    if unknown:
        raise ValueError("Неизвестные проверки: " + ", ".join(sorted(unknown)))
    results = [
        GateResult(name, description, type(evidence.get(name)) is bool and evidence[name] is True)
        for name, description in GATES
    ]
    return {
        "ready_for_production": all(result.passed for result in results),
        "pending": [result.name for result in results if not result.passed],
        "gates": [
            {"id": result.name, "description": result.description, "passed": result.passed}
            for result in results
        ],
        "note": "Решение основано только на переданных доказательствах. Самопроверка VPS не выполняется.",
    }
