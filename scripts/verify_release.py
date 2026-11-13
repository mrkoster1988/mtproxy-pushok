#!/usr/bin/env python3
"""Безопасная локальная проверка исходников Pushok без деплоя.

Offline: только стандартная библиотека, не требует Docker/БД/сети.
Full: только явный тестовый PostgreSQL + разрешение оператора.
Ни один результат этой утилиты сам по себе не означает приёмку релиза.
"""
import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

CHECK_DIRS = ("api", "bot", "scripts", "tests")
LOOPBACK_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


def allowed_test_dsn(dsn, *, ci=False):
    """Только явный тестовый endpoint; localhost сам по себе не гарантия.

    Защищает от случайной ошибки адреса, но не от намеренно
    перенаправленного порта. Перед запуском проверить инстанс PostgreSQL.
    """
    if not isinstance(dsn, str) or not dsn.startswith(("postgresql://", "postgres://")):
        return False
    try:
        url = urlsplit(dsn)
        host = (url.hostname or "").lower()
        if host not in LOOPBACK_HOSTS and not (ci and host == "postgres"):
            return False
        if url.port is not None and not 1 <= url.port <= 65535:
            return False
        if url.path != "/postgres" or not url.username or url.password is None:
            return False
        if url.fragment:
            return False
        return True
    except ValueError:
        return False

def result(name, passed, detail):
    return {"name": name, "passed": bool(passed), "detail": detail}


def run_command(command, *, root, env, timeout=900):
    """В отчёт идёт лишь код выхода, а не stdout/stderr с возможными секретами."""
    try:
        proc = subprocess.run(command, cwd=root, env=env, capture_output=True,
                              check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result("command", False, type(exc).__name__)
    return result("command", proc.returncode == 0, f"exit_code={proc.returncode}")


def verify(root, mode="offline", *, allow_test_db=False, env=None):
    if mode not in ("offline", "full"):
        raise ValueError("invalid_mode")
    root = Path(root).resolve()
    env = dict(os.environ if env is None else env)
    checks = []
    checked = 0
    errors = []
    for directory in CHECK_DIRS:
        base = root / directory
        if not base.is_dir():
            errors.append(f"missing:{directory}")
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in (".venv", "__pycache__", ".build") for part in path.parts):
                continue
            checked += 1
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeError, OSError) as exc:
                errors.append(f"{path.relative_to(root)}:{type(exc).__name__}")
    checks.append(result("python_syntax", checked > 0 and not errors,
                         f"files={checked}; errors={','.join(errors[:10])}"))

    # Режим offline не должен наследовать реальные реквизиты БД.
    offline_env = dict(env)
    offline_env.pop("DATABASE_URL", None)
    offline_env.pop("TEST_ADMIN_DSN", None)
    for name, command in (
        ("repository_preflight", [sys.executable, "scripts/preflight.py", "--json"]),
        ("preflight_unit_tests", [sys.executable, "-m", "unittest", "discover",
                                  "-s", "tests", "-p", "test_preflight.py", "-v"]),
    ):
        outcome = run_command(command, root=root, env=offline_env)
        outcome["name"] = name
        checks.append(outcome)

    if mode == "full":
        dsn = env.get("TEST_ADMIN_DSN", "")
        allowed = (
            allow_test_db
            and env.get("PUSHOK_TEST_DB_ALLOWED") == "1"
            and allowed_test_dsn(dsn, ci=env.get("CI") == "true")
        )
        checks.append(result("isolated_test_database", allowed,
                             "explicit_opt_in_marker_and_test_postgres_required"))
        suite_exists = (root / "tests/test_postgres.py").is_file()
        checks.append(result("postgres_integration_suite_present", suite_exists,
                             "tests/test_postgres.py must exist"))
        if allowed and suite_exists and all(item["passed"] for item in checks):
            safe_env = dict(env)
            safe_env["DATABASE_URL"] = dsn
            safe_env["PUSHOK_TEST_DB_ALLOWED"] = "1"
            full = run_command([sys.executable, "-m", "unittest", "discover", "-s",
                                "tests", "-v"], root=root, env=safe_env, timeout=1200)
            full["name"] = "all_python_tests_with_postgresql"
            checks.append(full)
        else:
            checks.append(result("all_python_tests_with_postgresql", False,
                                 "not_executed: prerequisite_failed"))
    passed = all(item["passed"] for item in checks)
    return {
        "mode": mode, "checks": checks, "passed": sum(x["passed"] for x in checks),
        "total": len(checks), "checks_passed": passed, "release_accepted": False,
        "ci_verified": False, "vps_verified": False, "sponsor_verified": False,
        "status": "local_checks_passed_only" if passed else "checks_failed",
    }


def main():
    parser = argparse.ArgumentParser(description="Локальная проверка исходников Pushok")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("offline", "full"), default="offline")
    parser.add_argument("--allow-test-db", action="store_true",
                        help="Явно разрешить создание и удаление временной базы на проверенном тестовом PostgreSQL")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = verify(args.root, mode=args.mode, allow_test_db=args.allow_test_db)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            print(("OK  " if item["passed"] else "ERR ") + item["name"] + ": " + item["detail"])
        print(f"Локальные проверки: {report['passed']}/{report['total']}. Приёмка релиза: НЕ подтверждена.")
    return 0 if report["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
