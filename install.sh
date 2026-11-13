#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then echo "Запустите от root: sudo bash install.sh" >&2; exit 1; fi
if ! command -v apt-get >/dev/null 2>&1; then echo "Поддерживается Ubuntu/Debian с apt-get." >&2; exit 1; fi
PROXY_HOST=""
if [[ "$#" -gt 0 ]]; then PROXY_HOST="$1"; fi
if [[ -z "$PROXY_HOST" ]]; then read -r -p "Домен или IPv4 MTProxy [mtproxy.pushok.shop]: " PROXY_HOST; fi
if [[ -z "$PROXY_HOST" ]]; then PROXY_HOST="mtproxy.pushok.shop"; fi
if [[ ! "$PROXY_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then echo "Укажите домен или IPv4 без схемы, порта и пробелов." >&2; exit 1; fi
read -r -p "Внешний TCP-порт [8443]: " PROXY_PORT
if [[ -z "$PROXY_PORT" ]]; then PROXY_PORT=8443; fi
if [[ ! "$PROXY_PORT" =~ ^[0-9]+$ ]] || (( PROXY_PORT < 1 || PROXY_PORT > 65535 )); then echo "Порт должен быть числом от 1 до 65535." >&2; exit 1; fi

ENV_FILE=/etc/mtproxy-pushok/mtproxy.env
old_value() {
  local key="$1"
  [[ -r "$ENV_FILE" ]] || return 0
  sed -n "s/^$key=//p" "$ENV_FILE" | head -n 1
}
OLD_TAG="$(old_value MT_PROXY_TAG)"
read -r -p "Тег спонсора из @MTProxybot [$OLD_TAG]: " PROXY_TAG
if [[ -z "$PROXY_TAG" ]]; then PROXY_TAG="$OLD_TAG"; fi
if [[ -n "$PROXY_TAG" && ! "$PROXY_TAG" =~ ^[A-Fa-f0-9]{32}$ ]]; then echo "Тег должен быть 32-значным hex-кодом из @MTProxybot." >&2; exit 1; fi

if ! command -v ss >/dev/null 2>&1; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y iproute2; fi
if systemctl is-active --quiet mtproxy-pushok.service; then
  echo "MTProxy Pushok уже работает; обновлю его конфигурацию."
elif ss -lntH "( sport = :$PROXY_PORT )" | grep -q .; then
  echo "TCP-порт $PROXY_PORT уже занят. Освободите порт или выберите другой." >&2; exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -o DPkg::Lock::Timeout=600 install -y git curl build-essential libssl-dev zlib1g-dev openssl
if [[ -d /opt/MTProxy/.git ]]; then
  git -C /opt/MTProxy pull --ff-only
elif [[ -e /opt/MTProxy ]]; then
  echo "/opt/MTProxy уже существует и не является исходным Git-репозиторием; остановка без перезаписи." >&2; exit 1
else
  git clone --depth 1 https://github.com/TelegramMessenger/MTProxy.git /opt/MTProxy
fi
make -C /opt/MTProxy

install -d -m 0750 /etc/mtproxy-pushok
install -d -m 0755 /var/lib/mtproxy-pushok
tmp_secret="$(mktemp /etc/mtproxy-pushok/proxy-secret.XXXXXX)"
tmp_config="$(mktemp /etc/mtproxy-pushok/proxy-multi.conf.XXXXXX)"
trap 'rm -f "$tmp_secret" "$tmp_config"' EXIT
curl --fail --location --silent --show-error https://core.telegram.org/getProxySecret -o "$tmp_secret"
curl --fail --location --silent --show-error https://core.telegram.org/getProxyConfig -o "$tmp_config"
[[ -s "$tmp_secret" && -s "$tmp_config" ]] || { echo "Telegram не вернул файлы конфигурации." >&2; exit 1; }
install -o root -g root -m 0644 "$tmp_secret" /etc/mtproxy-pushok/proxy-secret
install -o root -g root -m 0644 "$tmp_config" /etc/mtproxy-pushok/proxy-multi.conf

PROXY_SECRET="$(old_value MT_PROXY_SECRET)"
if [[ -z "$PROXY_SECRET" ]]; then PROXY_SECRET="$(openssl rand -hex 16)"; fi
if [[ ! "$PROXY_SECRET" =~ ^[A-Fa-f0-9]{32}$ ]]; then echo "Существующий секрет повреждён. Проверьте $ENV_FILE; секрет не будет заменён автоматически." >&2; exit 1; fi
umask 077
cat > "$ENV_FILE" <<EOF
MT_PROXY_HOST=$PROXY_HOST
MT_PROXY_PORT=$PROXY_PORT
MT_PROXY_SECRET=$PROXY_SECRET
MT_PROXY_TAG=$PROXY_TAG
EOF
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

cat > /usr/local/sbin/mtproxy-pushok-run <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
source /etc/mtproxy-pushok/mtproxy.env
set -- -u nobody -p 8888 -H "$MT_PROXY_PORT" -S "$MT_PROXY_SECRET"
if [[ -n "$MT_PROXY_TAG" ]]; then set -- "$@" -P "$MT_PROXY_TAG"; fi
set -- "$@" --aes-pwd /etc/mtproxy-pushok/proxy-secret /etc/mtproxy-pushok/proxy-multi.conf -M 1
exec /opt/MTProxy/objs/bin/mtproto-proxy "$@"
EOF
chmod 0755 /usr/local/sbin/mtproxy-pushok-run

cat > /usr/local/sbin/mtproxy-pushok-update-config <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
tmp="$(mktemp /etc/mtproxy-pushok/proxy-multi.conf.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
curl --fail --location --silent --show-error https://core.telegram.org/getProxyConfig -o "$tmp"
[[ -s "$tmp" ]] || { echo "Telegram вернул пустую конфигурацию MTProxy." >&2; exit 1; }
install -o root -g root -m 0644 "$tmp" /etc/mtproxy-pushok/proxy-multi.conf
systemctl try-restart mtproxy-pushok.service
EOF
chmod 0755 /usr/local/sbin/mtproxy-pushok-update-config

cat > /etc/systemd/system/mtproxy-pushok.service <<'EOF'
[Unit]
Description=MTProxy Pushok
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/local/sbin/mtproxy-pushok-run
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/mtproxy-pushok
ReadWritePaths=/var/lib/mtproxy-pushok
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/mtproxy-pushok-update-config.service <<'EOF'
[Unit]
Description=Refresh Telegram MTProxy core configuration
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/mtproxy-pushok-update-config
EOF
cat > /etc/systemd/system/mtproxy-pushok-update-config.timer <<'EOF'
[Unit]
Description=Daily refresh of Telegram MTProxy core configuration
[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now mtproxy-pushok.service
systemctl enable --now mtproxy-pushok-update-config.timer
sleep 2
if ! systemctl is-active --quiet mtproxy-pushok.service; then
  systemctl --no-pager --full status mtproxy-pushok.service || true
  echo "Проверьте журнал: journalctl -u mtproxy-pushok -n 100." >&2; exit 1
fi
echo
echo "MTProxy Pushok запущен на TCP-порту $PROXY_PORT."
echo "Ссылка для Telegram: tg://proxy?server=$PROXY_HOST&port=$PROXY_PORT&secret=dd$PROXY_SECRET"
echo "Журнал: journalctl -u mtproxy-pushok -f"
echo "Зарегистрируйте прокси через @MTProxybot (/newproxy); установщик примет выданный тег при повторном запуске."

if [[ -f "$(dirname "$0")/scripts/repair_proxy.py" ]]; then python3 "$(dirname "$0")/scripts/repair_proxy.py"; fi
