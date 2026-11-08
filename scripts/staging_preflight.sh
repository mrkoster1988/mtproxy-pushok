#!/usr/bin/env bash
# Предпусковая проверка MTProxy Pushok. Ничего не запускает и не меняет.
set -euo pipefail
cd "$(dirname "$0")/.."

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
import re
import sys

values = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")

required = (
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "DATABASE_URL", "BOT_INTERNAL_SECRET", "TELEGRAM_BOT_TOKEN",
    "SESSION_SIGNING_KEY",
)
missing = [k for k in required if not values.get(k) or values[k].startswith("REPLACE_")]
if missing:
    print("Отсутствуют или не настроены поля: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
if not re.fullmatch(r"[0-9a-f]{64}", values["BOT_INTERNAL_SECRET"]):
    print("BOT_INTERNAL_SECRET должен быть из 64 строчных hex-символов.", file=sys.stderr)
    sys.exit(1)
if len(values["SESSION_SIGNING_KEY"]) < 32:
    print("SESSION_SIGNING_KEY слишком короткий.", file=sys.stderr)
    sys.exit(1)
if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", values["TELEGRAM_BOT_TOKEN"]):
    print("Неверный формат TELEGRAM_BOT_TOKEN.", file=sys.stderr)
    sys.exit(1)
print("Локальная конфигурация: базовые поля и формат секретов проверены.")
PY

docker compose --profile bot config --quiet
echo "Docker Compose: конфигурация валидна."
echo "Проверка завершена: контейнеры, сеть, БД и Telegram не изменялись."
