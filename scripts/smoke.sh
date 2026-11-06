#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
# Имя нельзя передать извне: очистка относится только к нашему новому стенду.
PROJECT="pushok-smoke-$(date +%s)-$$"
compose() { docker compose --project-directory "$ROOT" -p "$PROJECT" -f "$ROOT/compose.smoke.yaml" "$@"; }
cleanup() { compose down --volumes --remove-orphans; }
on_exit() {
  code=$?
  if (( code != 0 )); then compose logs --no-color --tail=100 db api || true; fi
  cleanup || true
  exit "$code"
}
trap on_exit EXIT
docker compose version >/dev/null
compose config --quiet
compose up -d --build --wait --wait-timeout 180 db api
compose run --rm backupcheck
# Тома сохраняются при повторном запуске; проверим их после restart.
compose restart db api
compose up -d --wait --wait-timeout 180 db api
compose run --build --rm checks
echo "SMOKE OK: миграции, backup/restore, restart, HTTP API, цикл кампании."
