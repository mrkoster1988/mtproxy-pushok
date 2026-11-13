#!/usr/bin/env python3
"""Read-only .env/Compose checks. No network requests or printed secret values."""
import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

FLAGS = (
    "PUSHOK_TELEGRAM_TEST_ENVIRONMENT", "PUSHOK_MINIAPP_ENABLED",
    "PUSHOK_STARS_PAYMENTS_ENABLED", "PUSHOK_SBP_PAYMENTS_ENABLED",
    "PUSHOK_SBP_RECONCILIATION_ENABLED", "PUSHOK_SBP_WEBHOOK_ENABLED",
    "PUSHOK_SBP_TEST_MODE", "PUSHOK_BUTTON_ICONS_ENABLED",
)
REQUIRED = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
            "BOT_INTERNAL_SECRET", "SESSION_SIGNING_KEY", "TELEGRAM_BOT_TOKEN")


class ConfigError(ValueError):
    """Only fixed, non-secret messages may be passed to this exception."""


def parse_env(text):
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ConfigError(f"Строка {number}: неверное присваивание или повтор ключа.")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise ConfigError(f"Строка {number}: неверные кавычки.")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if any(char.isspace() for char in value) or "'" in value or '"' in value:
                raise ConfigError(f"Строка {number}: значение нужно заключить в кавычки.")
        if any(char in value for char in "$`\\") or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ConfigError(f"Строка {number}: подстановки, escape-последовательности и управляющие символы запрещены.")
        values[key] = value
    return values


def read_env(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in (0o400, 0o600) or info.st_uid != os.geteuid():
            raise ConfigError(".env должен быть обычным файлом текущего владельца с правами 0600 или 0400.")
        if info.st_size > 65536:
            raise ConfigError(".env превышает 64 КиБ.")
        text = source.read(65537)
        if len(text) > 65536:
            raise ConfigError(".env превышает допустимый размер.")
        return parse_env(text)


def valid_host(value):
    return bool(len(value) <= 253 and re.fullmatch(
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", value))


def valid_miniapp(value):
    try:
        url = urlsplit(value)
        return bool(url.scheme == "https" and valid_host(url.hostname or "")
                    and url.username is None and url.password is None
                    and url.port in (None, 443) and url.path in ("/miniapp", "/miniapp/")
                    and not url.query and not url.fragment
                    and not any(c.isspace() for c in value))
    except ValueError:
        return False


def validate_values(values, add, *, allow_telegram_deferred=False):
    deferred = (allow_telegram_deferred and not values.get("TELEGRAM_BOT_TOKEN")
                and not values.get("TELEGRAM_BOT_USERNAME")
                and all(values.get(key, "0") == "0" for key in (
                    "PUSHOK_MINIAPP_ENABLED", "PUSHOK_STARS_PAYMENTS_ENABLED", "PUSHOK_TELEGRAM_TEST_ENVIRONMENT")))
    required = [key for key in REQUIRED if not (deferred and key == "TELEGRAM_BOT_TOKEN")]
    missing = [key for key in required if not values.get(key) or values[key].upper().startswith("REPLACE_")]
    add("required", not missing, "Обязательные поля настроены." if not missing else "Настройте: " + ", ".join(missing))
    secrets = [values.get(key, "") for key in ("POSTGRES_PASSWORD", "SESSION_SIGNING_KEY", "BOT_INTERNAL_SECRET")]
    add("secrets", all(len(value) >= 32 for value in secrets) and len(set(secrets)) == len(secrets)
        and bool(re.fullmatch(r"[0-9a-f]{64}", values.get("BOT_INTERNAL_SECRET", ""))),
        "Пароль БД и ключ сессии: минимум 32 символа; внутренний ключ: 64 hex. Все три секрета различаются.")
    try:
        dsn = urlsplit(values.get("DATABASE_URL", ""))
        db_ok = (dsn.scheme == "postgresql" and dsn.hostname == "db" and dsn.port == 5432
                 and unquote(dsn.username or "") == values.get("POSTGRES_USER")
                 and unquote(dsn.password or "") == values.get("POSTGRES_PASSWORD")
                 and unquote(dsn.path) == "/" + values.get("POSTGRES_DB", "")
                 and not dsn.query and not dsn.fragment
                 and all(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", values.get(key, ""))
                         for key in ("POSTGRES_DB", "POSTGRES_USER")))
    except ValueError:
        db_ok = False
    add("database", db_ok, "DATABASE_URL должен совпадать с POSTGRES_* и указывать на db:5432.")
    port = values.get("API_BIND_PORT", "8000")
    add("api_port", bool(re.fullmatch(r"[0-9]{1,5}", port)) and 1024 <= int(port) <= 65535,
        "API_BIND_PORT: целое число от 1024 до 65535.")
    add("project_name", bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", values.get("COMPOSE_PROJECT_NAME", "mtproxy-pushok"))),
        "Имя Compose-проекта: до 63 строчных букв, цифр, дефисов и подчёркиваний.")
    add("telegram", deferred or (bool(re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{10,256}", values.get("TELEGRAM_BOT_TOKEN", "")))
        and bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", values.get("TELEGRAM_BOT_USERNAME", "mtproxy_pushok_bot")))),
        "Telegram отложен: вход, бот, Mini App и Stars не проверены." if deferred else
        "Проверьте формат токена и username бота (без @). Подлинность токена здесь не проверяется.")
    invalid = [key for key in FLAGS if values.get(key, "0") not in ("0", "1")]
    add("feature_flags", not invalid, "Флаги должны быть 0 или 1." + (" Исправьте: " + ", ".join(invalid) if invalid else ""))
    add("miniapp", values.get("PUSHOK_MINIAPP_ENABLED", "0") != "1" or valid_miniapp(values.get("PUSHOK_MINIAPP_URL", "")),
        "Включённая Mini App требует https://домен/miniapp без параметров, userinfo и нестандартного порта.")
    active = any(values.get(key) == "1" for key in (
        "PUSHOK_SBP_PAYMENTS_ENABLED", "PUSHOK_SBP_RECONCILIATION_ENABLED", "PUSHOK_SBP_WEBHOOK_ENABLED"))
    shop, key = values.get("YOOKASSA_SHOP_ID", ""), values.get("YOOKASSA_SECRET_KEY", "")
    add("sbp_credentials", not active or (bool(re.fullmatch(r"[0-9]+", shop))
        and bool(key) and not key.upper().startswith("REPLACE_")
        and valid_host(values.get("PUSHOK_PUBLIC_PAYMENT_HOST", ""))),
        "Для СБП нужны числовой shop ID, секрет провайдера и DNS-домен возврата без https:// и пути.")
    # Reconciliation can continue when new checkout is paused.
    add("sbp_webhook", values.get("PUSHOK_SBP_WEBHOOK_ENABLED") != "1"
        or values.get("PUSHOK_SBP_RECONCILIATION_ENABLED") == "1",
        "Webhook СБП требует включённой серверной сверки; выдача новых счетов может быть выключена.")


def compose_policy(config, values):
    services = config["services"]
    for name in ("db", "api", "bot", "sbp-worker"):
        service = services[name]
        if service.get("network_mode") or service.get("privileged"):
            return False
        if name != "api" and service.get("ports"):
            return False
    ports = services["api"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or ports[0].get("target") != 8000:
        return False
    if str(ports[0].get("published")) != values.get("API_BIND_PORT", "8000"):
        return False
    expected = {"db": ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"),
                "api": REQUIRED + FLAGS + ("PUSHOK_MINIAPP_URL", "PUSHOK_PUBLIC_PAYMENT_HOST", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY"),
                "sbp-worker": ("DATABASE_URL", "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY") + FLAGS,
                "bot": ("TELEGRAM_BOT_TOKEN", "BOT_INTERNAL_SECRET", "TELEGRAM_BOT_USERNAME",
                        "PUSHOK_TELEGRAM_TEST_ENVIRONMENT", "PUSHOK_MINIAPP_ENABLED", "PUSHOK_MINIAPP_URL")}
    defaults = {key: "0" for key in FLAGS}
    defaults.update(PUSHOK_SBP_TEST_MODE="1", TELEGRAM_BOT_USERNAME="mtproxy_pushok_bot")
    for name, keys in expected.items():
        actual = services[name].get("environment", {})
        for key in keys:
            expected_value = values.get(key, defaults.get(key, ""))
            if name == "bot" and key == "TELEGRAM_BOT_USERNAME" and not expected_value:
                expected_value = defaults[key]  # Compose использует ${VAR:-default}.
            if actual.get(key, defaults.get(key, "")) != expected_value:
                return False
    return True


def command_environment():
    return {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_CONFIG") if key in os.environ}


def compose_command(root, values):
    command = ["docker", "compose", "--project-directory", str(root), "--env-file", str(root / ".env"),
               "--project-name", values.get("COMPOSE_PROJECT_NAME", "mtproxy-pushok"), "-f", str(root / "compose.yaml")]
    if (root / "compose.override.yaml").exists():
        command.extend(["-f", str(root / "compose.override.yaml")])
    return command


def inspect(root, *, config_only=False, allow_telegram_deferred=False):
    root = Path(root).resolve()
    checks = []

    def add(name, passed, details):
        checks.append({"check": name, "passed": bool(passed), "details": details})

    # Ignore inherited application/Compose/Git overrides; use this directory.
    env = command_environment()
    values = {}
    try:
        values = read_env(root / ".env")
        add("env_file", True, ".env прочитан без исполнения; права и синтаксис проверены.")
    except (OSError, UnicodeError):
        add("env_file", False, "Не удалось прочитать обычный .env без символьной ссылки. Проверьте наличие, владельца и UTF-8.")
    except ConfigError as exc:
        add("env_file", False, str(exc))
    if checks[-1]["passed"]:
        validate_values(values, add, allow_telegram_deferred=allow_telegram_deferred)
    try:
        tracked = subprocess.run(["git", "ls-files", "--", ".env"], cwd=root, env=env,
                                 capture_output=True, timeout=10, check=False)
        add("env_not_tracked", tracked.returncode == 0 and not tracked.stdout.strip(),
            ".env не должен отслеживаться Git. Проверка требует Git и клона репозитория.")
    except (OSError, subprocess.TimeoutExpired):
        add("env_not_tracked", False, "Не удалось проверить индекс Git за 10 секунд.")
    compose_checked = False
    if not config_only and all(check["passed"] for check in checks):
        command = compose_command(root, values)
        command.extend(["--profile", "bot", "config", "--format", "json"])
        try:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=20, check=False)
            compose_checked = True
            good = result.returncode == 0 and compose_policy(json.loads(result.stdout), values)
            add("compose", good, "Compose должен совпадать с .env: API только 127.0.0.1, БД/бот/worker без опубликованных портов и host network.")
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
            add("compose", False, "Compose не прошёл проверку за 20 секунд. Проверьте Docker Compose plugin и YAML. Вывод скрыт для защиты секретов.")
    return {"schema_version": 1, "scope": "configuration_only" if config_only else "configuration_and_compose",
            "ok": all(check["passed"] for check in checks), "checks": checks,
            "compose_checked": compose_checked, "changes_applied": False,
            "telegram_verified": False, "database_verified": False, "admin_verified": False,
            "payments_verified": False, "sponsor_verified": False, "release_accepted": False}


def main():
    parser = argparse.ArgumentParser(description="Предпусковая проверка MTProxy Pushok без изменений и вывода секретов")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config-only", action="store_true", help="Проверить .env и индекс Git без Docker")
    parser.add_argument("--json", action="store_true", help="Отчёт JSON без значений конфигурации")
    parser.add_argument("--allow-telegram-deferred", action="store_true", help="Разрешить пустые токен/username только при выключенных Mini App, Stars и тестовой среде")
    args = parser.parse_args()
    report = inspect(args.root, config_only=args.config_only, allow_telegram_deferred=args.allow_telegram_deferred)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            print(("OK  " if check["passed"] else "ERR ") + check["check"] + " — " + check["details"])
        print("Compose: " + ("проверен" if report["compose_checked"] else "не проверен"))
        print("Сервисы не запускались. БД, администратор, Telegram, платежи и спонсор требуют отдельной приёмки.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
