#!/usr/bin/env bash
set -Eeuo pipefail
# Диалог читает терминал отдельно от потока загрузки скрипта.
if [[ "$*" == "--help" ]]; then
  echo "sudo bash setup.sh [--plan] — мастер установки MTProxy Pushok"
  exit 0
fi
PLAN=false
[[ "$*" != "--plan" ]] || PLAN=true
ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if ! { exec 3</dev/tty; } 2>/dev/null; then exec 3<&0; fi
trap 'echo "Установка остановлена. Существующие данные не удалены; проверьте сообщение выше." >&2' ERR
choose() {
  local target="$1" title="$2" fallback="$3" answer
  shift 3
  echo >&2
  echo "$title" >&2
  local number=1
  for item in "$@"; do echo "  $number) $item" >&2; number=$((number+1)); done
  while true; do
    read -r -u 3 -p "Выберите номер [$fallback]: " answer
    answer="$(printf '%s' "$answer" | tr -d '\r')"
    [[ -n "$answer" ]] || answer="$fallback"
    if [[ "$answer" =~ ^[1-9]$ ]] && ((answer>=1 && answer<number)); then
      printf -v "$target" '%s' "$answer"; return
    fi
    echo "Введите номер из списка." >&2
  done
}
ask() {
  local target="$1" title="$2" fallback="$3" input_value
  read -r -u 3 -p "$title [$fallback]: " input_value
  [[ -n "$input_value" ]] || input_value="$fallback"
  printf -v "$target" '%s' "$input_value"
}
domain_input() {
  local target="$1" answer
  while true; do
    ask answer "Домен кабинета без https:// и пути" "ads.pushok.shop"
    if [[ "$answer" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]] && [[ "$answer" == *.* && "$answer" != *..* ]]; then
      printf -v "$target" '%s' "$answer"; return
    fi
    echo "Введите домен, например ads.example.ru." >&2
  done
}
install_docker() {
  if command -v docker >/dev/null; then
    docker compose version >/dev/null || { echo "В существующем Docker отсутствует Compose plugin. Установите его и повторите запуск."; exit 1; }
    docker info >/dev/null
    return
  fi
  . /etc/os-release
  [[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo "Автоустановка Docker поддерживает Ubuntu 24.04."; exit 1; }
  for pkg in docker.io podman-docker containerd runc; do
    if dpkg-query -W -f='$Status' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
      echo "Обнаружен $pkg. Разрешите конфликт пакетов Docker перед установкой."; exit 1
    fi
  done
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  arch="$(dpkg --print-architecture)"
  printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$arch" > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

configure_payments() {
  PAYMENT_B64=""
  choose PAYMENT_MODE "Реквизиты для ручной оплаты" 1 "Указать инструкцию и получателя сейчас" "Настроить позже / отключить показ реквизитов"
  if [[ "$PAYMENT_MODE" == 1 ]]; then
    while true; do
      ask PAYMENT_TEXT "Реквизиты, получатель и инструкция (одной строкой, без суммы)" ""
      if [[ -n "$PAYMENT_TEXT" ]] && (( $(printf '%s' "$PAYMENT_TEXT" | wc -m) <= 4000 )); then break; fi
      echo "Введите от 1 до 4000 символов." >&2
    done
    PAYMENT_B64="$(printf '%s' "$PAYMENT_TEXT" | base64 -w0)"
    unset PAYMENT_TEXT
  fi
  PAYMENT_CHANGED=true
}

echo "MTProxy Pushok v0.013 — мастер настройки" >&2
choose COMPONENTS "Что установить?" 3 "Только MTProxy" "Только спонсорский кабинет" "MTProxy и кабинет"
NEW_CONFIG=true
HTTPS=1
ADMIN_ID=""
PAYMENT_CHANGED=false
PAYMENT_B64=""
if [[ "$COMPONENTS" != 1 ]]; then
  if [[ -f "$ROOT_DIR/.env" ]]; then
    choose EXISTING "Найдена существующая настройка" 1 "Сохранить настройки и обновить сервисы" "Изменить инструкцию оплаты и обновить" "Завершить без изменений"
    [[ "$EXISTING" != 3 ]] || exit 0
    NEW_CONFIG=false
    if [[ "$EXISTING" == 2 ]]; then configure_payments; fi
  else
    choose INSTALL_MODE "Назначение установки" 1 "Основная установка" "Приватный staging (только localhost)"
    PROJECT_DEFAULT=mtproxy-pushok
    PORT_DEFAULT=8000
    if [[ "$INSTALL_MODE" == 2 ]]; then PROJECT_DEFAULT=mtproxy-pushok-staging; PORT_DEFAULT=8010; fi
    while true; do
      ask PROJECT_NAME "Уникальное имя Compose-проекта" "$PROJECT_DEFAULT"
      [[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]{0,39}$ ]] && break
      echo "Используйте строчные латинские буквы, цифры, дефис или подчёркивание." >&2
    done
    if [[ "$INSTALL_MODE" == 2 ]]; then
      HTTPS=3
      echo "Приватный staging: API будет доступен только на localhost." >&2
    else
      choose HTTPS "Как открыть кабинет?" 1 "Через существующий Nginx/Caddy (API на localhost)" "Новый Caddy с автоматическим HTTPS (свободные 80/443)" "Только локально, без домена"
    fi
    if [[ "$HTTPS" != 3 ]]; then domain_input CABINET_DOMAIN; fi
    while true; do
      ask API_PORT "Локальный порт API" "$PORT_DEFAULT"
      if [[ "$API_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && ((API_PORT<=65535)); then break; fi
      echo "Порт должен быть от 1 до 65535." >&2
    done
    choose TELEGRAM "Настроить Telegram-вход?" 1 "Настроить сейчас" "Позже (вход временно недоступен)"
    BOT_TOKEN=""
    BOT_USERNAME=""
    if [[ "$TELEGRAM" == 1 ]]; then
      while true; do
        read -r -s -u 3 -p "Токен бота из BotFather (скрыт): " BOT_TOKEN; echo >&2
        [[ "$BOT_TOKEN" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]] && break
        echo "Ожидается токен вида 123456:ABC..." >&2
      done
      while true; do
        ask BOT_USERNAME "Имя бота без @" ""
        [[ "$BOT_USERNAME" =~ ^[a-zA-Z0-9_]+$ ]] && break
      done
      choose ADMIN_SETUP "Назначить владельца кабинета?" 1 "Указать Telegram ID" "Настроить позже"
      if [[ "$ADMIN_SETUP" == 1 ]]; then
        while true; do
          ask ADMIN_ID "Числовой Telegram ID владельца" ""
          [[ "$ADMIN_ID" =~ ^[1-9][0-9]{0,17}$ ]] && break
        done
      fi
    fi
    configure_payments
  fi
fi
case "$COMPONENTS" in
  1) echo "Будет установлен MTProxy." >&2 ;;
  2) echo "Будет установлен кабинет." >&2 ;;
  3) echo "Будут установлены MTProxy и кабинет." >&2 ;;
esac
if [[ "$COMPONENTS" != 1 ]]; then
  if "$NEW_CONFIG"; then
    echo "Compose-проект: $PROJECT_NAME" >&2
    echo "Локальный порт API: $API_PORT" >&2
    if [[ "$HTTPS" != 3 ]]; then echo "Домен кабинета: $CABINET_DOMAIN" >&2; fi
    case "$HTTPS" in
      1) echo "Доступ: через существующий reverse proxy (его настройка вручную)." >&2 ;;
      2) echo "Доступ: новый Caddy, порты 80/443; DNS должен указывать на сервер." >&2 ;;
      3) echo "Доступ: только локальный." >&2 ;;
    esac
    [[ -z "$ADMIN_ID" ]] || echo "Telegram ID владельца: $ADMIN_ID" >&2
  else
    echo "Существующие настройки и секреты будут сохранены." >&2
  fi
fi
choose CONFIRM "Начать установку пакетов и запуск сервисов?" 2 "Начать" "Выйти без изменений"
[[ "$CONFIRM" == 1 ]] || exit 0
if "$PLAN"; then echo "План собран. Режим --plan: изменения не выполнялись."; exit 0; fi
[[ "$(id -u)" == 0 ]] || { echo "Запустите: sudo bash setup.sh"; exit 1; }
command -v apt-get >/dev/null || { echo "Требуется Ubuntu/Debian с apt."; exit 1; }
cd "$ROOT_DIR"
if [[ "$COMPONENTS" != 1 ]]; then
  if "$NEW_CONFIG"; then
    apt-get update
    apt-get install -y openssl curl iproute2
    if ss -lntH "( sport = :$API_PORT )" | grep -q .; then echo "Локальный порт $API_PORT занят."; exit 1; fi
    if [[ "$HTTPS" == 2 ]] && ss -lntH '( sport = :80 or sport = :443 )' | grep -q .; then
      echo "80/443 заняты. Выберите вариант с существующим reverse proxy."; exit 1
    fi
  fi
  install_docker
  if "$NEW_CONFIG"; then
    if [[ -n "$(docker ps -a -q --filter "label=com.docker.compose.project=$PROJECT_NAME")" ]] || [[ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT_NAME")" ]]; then
      echo "Compose-проект $PROJECT_NAME уже содержит ресурсы. Выберите другое имя; существующие данные не изменены."
      exit 1
    fi
  fi
  if "$NEW_CONFIG"; then
    umask 077
    DB_PASSWORD="$(openssl rand -hex 32)"
    SESSION_KEY="$(openssl rand -hex 32)"
    cat > .env <<EOF
COMPOSE_PROJECT_NAME=$PROJECT_NAME
POSTGRES_DB=mtproxy_pushok
POSTGRES_USER=mtproxy_pushok
POSTGRES_PASSWORD=$DB_PASSWORD
DATABASE_URL=postgresql://mtproxy_pushok:$DB_PASSWORD@db:5432/mtproxy_pushok
SESSION_SIGNING_KEY=$SESSION_KEY
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_BOT_USERNAME=$BOT_USERNAME
API_BIND_PORT=$API_PORT
PAYMENT_INSTRUCTIONS_B64=$PAYMENT_B64
EOF
    chmod 0600 .env
    if [[ "$HTTPS" == 2 ]]; then
      printf '%s {\n reverse_proxy api:8000\n}\n' "$CABINET_DOMAIN" > Caddyfile
      cat > compose.override.yaml <<'EOF'
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - mtproxy-pushok-caddy-data:/data
      - mtproxy-pushok-caddy-config:/config
    networks:
      - internal
      - edge
volumes:
  mtproxy-pushok-caddy-data:
  mtproxy-pushok-caddy-config:
networks:
  edge:
EOF
    fi
  fi
  command -v curl >/dev/null || { apt-get update; apt-get install -y curl; }
  if "$PAYMENT_CHANGED" && ! "$NEW_CONFIG"; then
    umask 077
    payment_env="$(mktemp .env.payments.XXXXXX)"
    sed '/^PAYMENT_INSTRUCTIONS_B64=/d' .env > "$payment_env"
    printf 'PAYMENT_INSTRUCTIONS_B64=%s\n' "$PAYMENT_B64" >> "$payment_env"
    chmod 0600 "$payment_env"
    mv "$payment_env" .env
  fi
  docker compose config --quiet
  docker compose up -d db
  ready=false
  for attempt in $(seq 1 60); do
    if docker compose exec -T db sh -c 'pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null; then ready=true; break; fi
    sleep 2
  done
  "$ready" || { echo "PostgreSQL не готов. Проверьте docker compose logs db."; exit 1; }
  docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < database/002_telegram_auth.sql
  if [[ -n "$ADMIN_ID" ]]; then
    printf "INSERT INTO users (telegram_user_id, role) VALUES (%s, 'admin') ON CONFLICT (telegram_user_id) DO UPDATE SET role='admin';\n" "$ADMIN_ID" |
      docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
  fi
  docker compose up --build -d
  port="$(sed -n 's/^API_BIND_PORT=//p' .env)"
  [[ -n "$port" ]] || port=8000
  ready=false
  for attempt in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null; then ready=true; break; fi
    sleep 2
  done
  "$ready" || { echo "API не готов. Проверьте docker compose logs api."; exit 1; }
  echo "Кабинет отвечает: http://127.0.0.1:$port"
  if [[ "$HTTPS" == 2 ]]; then echo "HTTPS: https://$CABINET_DOMAIN — проверьте DNS и доступ к 80/443."; fi
  if [[ "$HTTPS" == 1 && "$NEW_CONFIG" == true ]]; then echo "В существующем reverse proxy направьте https://$CABINET_DOMAIN на 127.0.0.1:$port."; fi
  echo "Домен входа Telegram зарегистрируйте у бота в BotFather."
fi
if [[ "$COMPONENTS" != 2 ]]; then
  echo "Далее: домен, TCP-порт и необязательный тег спонсора MTProxy."
  bash "$ROOT_DIR/install.sh" <&3
fi
echo "Настройка завершена. .env содержит секреты; права доступа 0600."
