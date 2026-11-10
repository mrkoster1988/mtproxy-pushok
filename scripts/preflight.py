#!/usr/bin/env python3
"""Проверки структуры MTProxy Pushok без сети, Docker, БД и доступа к секретам.

Проверка исходного кода, НЕ свидетельство прохождения функциональных тестов.
"""
import argparse
import json
import re
from pathlib import Path


def inspect(root: Path) -> dict:
    root = Path(root)
    checks = []

    def add(name: str, good: bool, details: str) -> None:
        checks.append({"check": name, "passed": bool(good), "details": details})

    def read(path: str) -> str:
        try:
            return (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    readme = read("README.md")
    version_match = re.search(r"(?m)^Версия проекта:\s*v(\d+\.\d+)\.$", readme)
    version = version_match.group(1) if version_match else None
    add("release_version", version is not None, "README.md должен объявлять версию проекта")
    if version:
        main = read("api/app/main.py")
        core = read("api/app/core_readiness.py")
        installer = read("setup.sh")
        web = read("api/web/index.html")
        changelog = read("CHANGELOG.md")
        values = re.findall(r'"project_version":\s*"([^\"]+)"', core)
        add("api_version", f'version="{version}"' in main, "Версия FastAPI должна совпадать с README")
        add("core_version", bool(values) and all(v == version for v in values), "Все project_version должны совпадать")
        add("installer_version", f"MTProxy Pushok v{version}" in installer, "Номер версии установщика")
        add("cabinet_version", f"v{version}</span></footer>" in web, "Версия в футере кабинета")
        add("changelog_version", f"## [{version}]" in changelog, "Запись в CHANGELOG")
    else:
        installer = read("setup.sh")

    sql = sorted((root / "database").glob("[0-9][0-9][0-9]_*.sql"))
    ids = [int(item.name.split("_", 1)[0]) for item in sql]
    add("migrations_contiguous", bool(ids) and ids == list(range(1, max(ids) + 1)),
        "Нумерация SQL-миграций должна быть последовательной с 001")
    expected = [p for p in sql if not p.name.startswith("001_")]
    positions = [installer.find("database/" + p.name) for p in expected]
    add("installer_migrations", bool(ids) and all(p >= 0 for p in positions) and positions == sorted(positions),
        "setup.sh должен применять миграции 002+ по порядку; 001 создаётся PostgreSQL при инициализации")

    github = read(".github/workflows/check.yml")
    gitlab = read(".gitlab-ci.yml")
    suite = "python -m unittest discover -s tests -v"
    add("github_test_suite", suite in github, "В GitHub Actions должна оставаться полная проверка unittest")
    add("gitlab_test_suite", suite in gitlab, "В GitLab CI должна оставаться полная проверка unittest")
    add("github_preflight", "python3 scripts/preflight.py" in github, "GitHub Actions должен запускать preflight")
    add("gitlab_preflight", "python3 scripts/preflight.py" in gitlab, "GitLab CI должен запускать preflight")
    add("report_routes", '/api/v1/campaigns/{campaign_id}/report"' in read("api/app/main.py")
        and '/api/v1/campaigns/{campaign_id}/report.csv"' in read("api/app/main.py"),
        "JSON и CSV отчёты должны оставаться в API")

    ok = all(check["passed"] for check in checks)
    return {"ok": ok, "version": version, "checks": checks,
            "passed": sum(check["passed"] for check in checks), "total": len(checks),
            "scope": "static_repository_only", "ci_executed": False, "vps_checked": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline preflight MTProxy Pushok")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Вывести машиночитаемый отчёт")
    args = parser.parse_args()
    report = inspect(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            print(("OK  " if check["passed"] else "ERR ") + check["check"] + " — " + check["details"])
        print(f"MTProxy Pushok {report['version'] or 'unknown'}: {report['passed']}/{report['total']} проверок")
        print("Важно: это статическая проверка. CI, PostgreSQL и VPS здесь не запускались.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
