#!/usr/bin/env bash
# Предпусковая проверка без запуска сервисов и раскрытия секретов.
set -euo pipefail
command -v python3 >/dev/null || { echo "Не найден python3" >&2; exit 1; }
exec python3 "$(dirname "$0")/staging_preflight.py" "$@"
