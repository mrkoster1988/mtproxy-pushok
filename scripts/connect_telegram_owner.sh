#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-connect}"
[[ $# -le 1 && "$action" =~ ^(connect|enable|disable|status|disconnect|check)$ ]] || {
  echo "Использование: sudo bash scripts/connect_telegram_owner.sh [enable|disable|connect|status|disconnect|check]" >&2
  exit 2
}
[[ "$(id -u)" == 0 ]] || { echo "Запустите через sudo на VPS." >&2; exit 1; }
if [[ "$action" =~ ^(connect|enable)$ && ( ! -t 0 || ! -t 1 ) ]]; then
  echo "Для входа нужен интерактивный терминал VPS." >&2; exit 2
fi
owner_source="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ "$action" == enable ]]; then
  [[ "$(dirname "$owner_source")" == /opt/mtproxy-pushok && -f /etc/mtproxy-pushok/mtproxy.env ]] || {
    echo "Автопривязка требует установленного на этом VPS MTProxy Classic и /opt/mtproxy-pushok." >&2; exit 1
  }
fi
owner_venv=/opt/mtproxy-pushok-telegram-owner-venv
umask 077
[[ ! -L "$owner_venv" ]] || { echo "Каталог окружения не должен быть ссылкой." >&2; exit 1; }
if [[ ! -d "$owner_venv" ]]; then
  mkdir -m 700 "$owner_venv"
fi
[[ "$(stat -c %u "$owner_venv")" == 0 && "$(stat -c %a "$owner_venv")" == 700 ]] || {
  echo "Окружение должно принадлежать root с правами 0700." >&2; exit 1
}
exec 9>"$owner_venv/.install.lock"
flock 9
if [[ ! -x "$owner_venv/bin/python" ]]; then
  python3 -m venv "$owner_venv" || {
    echo "Установите python3-venv: sudo apt-get install python3-venv; затем повторите команду." >&2
    exit 1
  }
fi
if ! "$owner_venv/bin/python" -c 'import telethon; assert telethon.__version__ == "1.45.0"' 2>/dev/null; then
  "$owner_venv/bin/python" -m pip install --disable-pip-version-check -r "$owner_source/requirements-telegram-owner.txt"
fi
flock -u 9
exec 9>&-
"$owner_venv/bin/python" "$owner_source/telegram_owner.py" "$action"
if [[ "$action" == enable ]]; then
  bash "$owner_source/install_promotion_agent.sh"
  echo "Автоматическая привязка включена. Состояние — в кабинете: Узлы и спонсоры → Автоприменение."
fi
