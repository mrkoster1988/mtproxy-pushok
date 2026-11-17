#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || exit 1
cd "$(dirname "$0")/.."
[[ "$(pwd -P)" == /opt/mtproxy-pushok ]] || exit 1
[[ -f /etc/mtproxy-pushok/mtproxy.env ]] || exit 0
# Установка зависимости выполняется явно, не в фоновом агенте.
if [[ "${1:-}" == --install-dependency ]] && [[ ! -x /usr/sbin/haproxy ]]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends haproxy
fi
install -d -m 755 /var/lib/mtproxy-pushok-gateway
cat > /etc/systemd/system/mtproxy-pushok-gateway.service <<'UNIT'
[Unit]
Description=Pushok common MTProxy TCP entry
After=network-online.target mtproxy-pushok.service
[Service]
Type=simple
DynamicUser=yes
RuntimeDirectory=mtproxy-pushok-gateway
RuntimeDirectoryMode=0700
LimitNOFILE=16384
ExecStart=/usr/sbin/haproxy -W -db -f /var/lib/mtproxy-pushok-gateway/haproxy.cfg
ExecReload=/bin/kill -USR2 $MAINPID
KillMode=mixed
TimeoutStopSec=10
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
UNIT
cat > /etc/systemd/system/mtproxy-pushok-gateway-agent.service <<'UNIT'
[Unit]
Description=Refresh Pushok gateway nodes and topics
After=docker.service network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/mtproxy-pushok
ExecStart=/usr/bin/python3 /opt/mtproxy-pushok/scripts/proxy_gateway.py
TimeoutStartSec=180
UMask=0077
UNIT
cat > /etc/systemd/system/mtproxy-pushok-gateway-agent.timer <<'UNIT'
[Unit]
Description=Check Pushok common entry
[Timer]
OnBootSec=45s
OnUnitInactiveSec=20s
AccuracySec=1s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now mtproxy-pushok-gateway-agent.timer
