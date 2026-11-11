#!/usr/bin/env bash
# Предпусковая проверка MTProxy Pushok. Ничего не запускает и не меняет.
set -euo pipefail
cd "$(dirname "$0")/.."

# Не запускать профиль bot, пока оператор не выбрал его явно.
# Режим без бота подходит для первого приватного стенда и SSH-кабинета.
MODE="${1:---cabinet-only}"
if [[ "$#" -gt 1 ]] || [[ "$MODE" != "--cabinet-only" && "$MODE" != "--with-bot" ]]; then
  echo "Использование: bash scripts/staging_preflight.sh [--cabinet-only|--with-bot]" >&2
  exit 2
fi
if [[ "$MODE" == "--with-bot" ]]; then
  export PUSHOK_PREFLIGHT_WITH_BOT=1
else
  export PUSHOK_PREFLIGHT_WITH_BOT=0
fi

echo "MTProxy Pushok — безопасная проверка staging (без изменений)"
command -v python3 >/dev/null || { echo "Не найден python3" >&2; exit 1; }
command -v docker >/dev/null || { echo "Не найден Docker" >&2; exit 1; }
docker compose version >/dev/null || { echo "Не найден Docker Compose" >&2; exit 1; }

if [[ ! -f .env ]]; then
  echo "Не найден локальный .env: сначала настройте его через мастер." >&2
  exit 1
fi
if command -v git >/dev/null && git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ОШИБКА: .env отслеживается Git — секреты нужно убрать из индекса." >&2
  exit 1
fi

# Секреты читаются локально, но никогда не печатаются.
python3 - <<'PY'
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re
import sys
import os

values = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")

required = (
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "DATABASE_URL", "BOT_INTERNAL_SECRET", "SESSION_SIGNING_KEY",
)
with_bot = os.environ.get("PUSHOK_PREFLIGHT_WITH_BOT") == "1"
if with_bot:
    required += ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME")
missing = [k for k in required if not values.get(k) or values[k].startswith("REPLACE_")]
if missing:
    print("Отсутствуют или не настроены поля: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
# Ни один секрет из .env не должен быть доступен другим пользователям.
if Path(".env").stat().st_mode & 0o077:
    print("Слишком открытые права .env: выполните chmod 600 .env.", file=sys.stderr)
    sys.exit(1)

# Это встроенный Docker Compose: API должен подключаться ТОЛЬКО к своей БД
# через внутреннее имя службы db, с теми же учётными данными.
try:
    url = urlsplit(values["DATABASE_URL"])
    valid_database = (
        url.scheme in ("postgresql", "postgres")
        and url.hostname == "db"
        and url.port == 5432
        and unquote(url.username or "") == values["POSTGRES_USER"]
        and unquote(url.password or "") == values["POSTGRES_PASSWORD"]
        and unquote(url.path) == "/" + values["POSTGRES_DB"]
        and not url.query and not url.fragment
    )
except ValueError:
    valid_database = False
if not valid_database:
    print("DATABASE_URL не соответствует внутреннему PostgreSQL из .env; внешняя БД запрещена для staging.", file=sys.stderr)
    sys.exit(1)

# Предпусковая проверка не должна случайно разрешать реальные счета Stars.
if values.get("PUSHOK_STARS_PAYMENTS_ENABLED", "0") not in ("", "0"):
    print("Для staging оставьте PUSHOK_STARS_PAYMENTS_ENABLED=0.", file=sys.stderr)
    sys.exit(1)

if not re.fullmatch(r"[0-9a-f]{64}", values["BOT_INTERNAL_SECRET"]):
    print("BOT_INTERNAL_SECRET должен быть из 64 строчных hex-символов.", file=sys.stderr)
    sys.exit(1)
if len(values["SESSION_SIGNING_KEY"]) < 32:
    print("SESSION_SIGNING_KEY слишком короткий.", file=sys.stderr)
    sys.exit(1)
token = values.get("TELEGRAM_BOT_TOKEN", "")
if with_bot or (token and not token.startswith("REPLACE_")):
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        print("Неверный формат TELEGRAM_BOT_TOKEN.", file=sys.stderr)
        sys.exit(1)
if with_bot and not re.fullmatch(r"[A-Za-z0-9_]{5,32}", values["TELEGRAM_BOT_USERNAME"]):
    print("Неверный формат TELEGRAM_BOT_USERNAME.", file=sys.stderr)
    sys.exit(1)
print("Локальная конфигурация: базовые поля и формат секретов проверены.")
print("Профиль бота: " + ("проверяется" if with_bot else "не выбран, токен не обязателен"))
PY

if [[ "$MODE" == "--with-bot" ]]; then
  docker compose --profile bot config --quiet
else
  docker compose config --quiet
fi

# Compose --quiet проверяет лишь схему. Проверяем фактически объединённый
# config (включая override.yaml), чтобы лишний сервис не открыл порт.
# JSON может содержать секреты — не записываем его в журнал или файл.
check_private_ports() {
  python3 -c '
import json
import sys

def deny(reason):
    print("STAGING_BLOCKED: " + reason, file=sys.stderr)
    raise SystemExit(1)

try:
    config = json.load(sys.stdin)
except (ValueError, UnicodeDecodeError):
    deny("compose_config_not_json")
services = config.get("services")
if not isinstance(services, dict) or not {"api", "db"}.issubset(services):
    deny("api_or_db_service_missing")

for service_name, service in services.items():
    if not isinstance(service, dict):
        deny("invalid_service_config")
    if service.get("network_mode") == "host" or service.get("privileged") is True:
        deny("host_network_or_privileged_service_forbidden")
    published = service.get("ports") or []
    if not isinstance(published, list):
        deny("invalid_ports")
    if service_name != "api" and published:
        deny("unexpected_published_service_port")
    for item in published:
        if not isinstance(item, dict):
            deny("unresolved_port_binding")
        host = item.get("host_ip")
        if host not in ("127.0.0.1", "::1") or item.get("target") != 8000:
            deny("public_or_unexpected_api_port")
api_ports = services["api"].get("ports") or []
if len(api_ports) != 1:
    deny("api_requires_single_loopback_binding")
print("Staging Compose: только API:8000 привязан к loopback, остальные порты не опубликованы.")
'
}
if [[ "$MODE" == "--with-bot" ]]; then
  docker compose --profile bot config --format json | check_private_ports
else
  docker compose config --format json | check_private_ports
fi
echo "Docker Compose: схема и приватные пробросы портов проверены."
echo "Проверка завершена: контейнеры, сеть, БД и Telegram не изменялись."
