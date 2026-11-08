#!/usr/bin/env bash
# Отдельная добровольная установка официального Docker MTProxy.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PLAN=false
[[ "${1:-}" != "--plan" ]] || PLAN=true
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--plan" ) ]]; then
  echo "Использование: sudo bash install_official_docker.sh [--plan]" >&2
  exit 2
fi
if ! { exec 3</dev/tty; } 2>/dev/null; then exec 3<&0; fi
choose() {
  local variable="$1" title="$2" fallback="$3" response item number
  shift 3
  echo >&2
  echo "$title" >&2
  number=1
  for item in "$@"; do echo "  $number) $item" >&2; number=$((number+1)); done
  while true; do
    read -r -u 3 -p "Выберите номер [$fallback]: " response
    [[ -n "$response" ]] || response="$fallback"
    if [[ "$response" =~ ^[1-9][0-9]*$ ]] && (( response < number )); then
      printf -v "$variable" '%s' "$response"
      return
    fi
    echo "Введите предложенный номер." >&2
  done
}
ask() {
  local variable="$1" question="$2" fallback="$3" response
  read -r -u 3 -p "$question [$fallback]: " response
  [[ -n "$response" ]] || response="$fallback"
  printf -v "$variable" '%s' "$response"
}
echo "MTProxy Pushok — официальный MTProxy Docker (изолированная установка)"
echo "ВНИМАНИЕ: telegrammessenger/proxy:latest — старый образ."
echo "До коммерческой эксплуатации проверьте его состояние и безопасность."
while true; do
  ask PROXY_HOST "Домен/IPv4 для будущей ссылки" "mtproxy.pushok.shop"
  [[ "$PROXY_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ && "$PROXY_HOST" != *..* ]] && break
  echo "Укажите только домен или IPv4 без схемы и порта." >&2
done
while true; do
  ask PROXY_PORT "TCP-порт на VPS" "8443"
  if [[ "$PROXY_PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$PROXY_PORT >= 1 && 10#$PROXY_PORT <= 65535 )); then
    PROXY_PORT="$((10#$PROXY_PORT))"
    break
  fi
  echo "Допустимый TCP-порт: 1–65535." >&2
done
choose BIND_MODE "Как слушать TCP-порт?" 2 \
  "Внешний адрес 0.0.0.0 (только для выделенного тестового VPS)" \
  "Только 127.0.0.1 (локальная проверка без публичного доступа)"
if [[ "$BIND_MODE" == 1 ]]; then BIND_IP="0.0.0.0"; else BIND_IP="127.0.0.1"; fi
choose TAG_MODE "Рекламный TAG из @MTProxyBot уже получен?" 2 \
  "Есть TAG — ввести скрыто" \
  "Нет — запуск без спонсорского TAG"
TAG=""
if [[ "$TAG_MODE" == 1 ]]; then
  while true; do
    read -r -s -u 3 -p "TAG (32 hex, ввод скрыт): " TAG
    echo >&2
    TAG="${TAG,,}"
    [[ "$TAG" =~ ^[0-9a-f]{32}$ && "$TAG" != 00000000000000000000000000000000 ]] && break
    echo "Нужен ненулевой 32-символьный TAG." >&2
  done
fi
choose WORKER_MODE "Количество процессов MTProxy" 2 \
  "1 процесс" "2 процесса (по умолчанию)" "4 процесса"
case "$WORKER_MODE" in
  1) WORKERS=1 ;;
  2) WORKERS=2 ;;
  3) WORKERS=4 ;;
esac
echo
echo "План: telegrammessenger/proxy:latest, $BIND_IP:$PROXY_PORT -> 443"
echo "Домен ссылки: $PROXY_HOST; WORKERS: $WORKERS"
if [[ -n "$TAG" ]]; then echo "TAG: указан, значение скрыто"; else echo "TAG: не указан"; fi
echo "Отдельный Compose-проект: mtproxy-pushok-official"
echo "Секреты: /etc/mtproxy-pushok/official-docker.env (root, 0600)"
echo "Никакие проекты Pushok VPN / RemnaShop не будут изменены."
choose CONFIRM "Создать и запустить тестовый Docker MTProxy?" 2 \
  "Установить тестовый контейнер" "Выйти без изменений"
[[ "$CONFIRM" == 1 ]] || exit 0
if "$PLAN"; then
  echo "План собран; режим --plan: ничего не изменено."
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || { echo "Требуется sudo/root." >&2; exit 1; }
command -v docker >/dev/null || { echo "Сначала установите Docker Engine." >&2; exit 1; }
docker compose version >/dev/null || { echo "Не найден Compose plugin." >&2; exit 1; }
docker info >/dev/null || { echo "Docker daemon недоступен." >&2; exit 1; }
command -v ss >/dev/null || { echo "Требуется iproute2 (команда ss)." >&2; exit 1; }
[[ -f "$ROOT_DIR/compose.proxy-official.yaml" ]] || {
  echo "Не найден compose.proxy-official.yaml." >&2; exit 1;
}
SETTINGS=/etc/mtproxy-pushok/official-docker.settings
SECRETS=/etc/mtproxy-pushok/official-docker.env
if [[ -e "$SETTINGS" || -e "$SECRETS" ]]; then
  echo "Официальный прокси уже настроен. Секреты и данные не перезаписываем." >&2
  exit 1
fi
if [[ -e /etc/mtproxy-pushok/mtproxy.env ]] || systemctl is-active --quiet mtproxy-pushok.service 2>/dev/null; then
  echo "Обнаружен классический MTProxy. Автоматическая замена запрещена." >&2
  exit 1
fi
if [[ -n "$(docker ps -a -q --filter label=com.docker.compose.project=mtproxy-pushok-official)" ]] ||
   [[ -n "$(docker volume ls -q --filter label=com.docker.compose.project=mtproxy-pushok-official)" ]]; then
  echo "Docker-ресурсы mtproxy-pushok-official уже существуют; не изменяем их." >&2
  exit 1
fi
if ss -lntH "( sport = :$PROXY_PORT )" | grep -q .; then
  echo "TCP-порт $PROXY_PORT занят; ничего не изменено." >&2
  exit 1
fi
umask 077
install -d -m 0700 /etc/mtproxy-pushok
PROXY_SECRET="$(openssl rand -hex 16)"
cat > "$SECRETS" <<EOF
SECRET=$PROXY_SECRET
WORKERS=$WORKERS
EOF
if [[ -n "$TAG" ]]; then printf 'TAG=%s\n' "$TAG" >> "$SECRETS"; fi
cat > "$SETTINGS" <<EOF
MTPROXY_HOST=$PROXY_HOST
MTPROXY_PORT=$PROXY_PORT
MTPROXY_BIND_IP=$BIND_IP
MTPROXY_SECRET_FILE=$SECRETS
EOF
chown root:root "$SECRETS" "$SETTINGS"
chmod 0600 "$SECRETS" "$SETTINGS"
unset TAG PROXY_SECRET
# config --quiet не выводит содержимое SECRET/TAG.
docker compose --project-name mtproxy-pushok-official \
  --env-file "$SETTINGS" -f "$ROOT_DIR/compose.proxy-official.yaml" config --quiet
docker compose --project-name mtproxy-pushok-official \
  --env-file "$SETTINGS" -f "$ROOT_DIR/compose.proxy-official.yaml" up -d
if [[ -z "$(docker compose --project-name mtproxy-pushok-official \
  --env-file "$SETTINGS" -f "$ROOT_DIR/compose.proxy-official.yaml" ps -q --status running proxy)" ]]; then
  echo "Контейнер не подтвердил запуск. Данные сохранены для диагностики." >&2
  exit 1
fi
echo "Контейнер запущен, но реальная доступность Telegram не проверена."
echo "Секрет подключения сохранён в $SECRETS; не публикуйте docker logs/inspect."
echo "Проверка в Telegram и регистрация через @MTProxyBot выполняются отдельно."
