#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || exit 1
cd "$(dirname "$0")/.."
[[ "$(pwd -P)" == /opt/mtproxy-pushok ]] || exit 1
[[ -f /etc/mtproxy-pushok/mtproxy.env ]] || exit 0
cat > /etc/systemd/system/mtproxy-pushok-promotion.service <<'UNIT'
[Unit]
Description=Apply prepared Pushok sponsor tags
After=docker.service mtproxy-pushok.service
[Service]
Type=oneshot
WorkingDirectory=/opt/mtproxy-pushok
ExecStart=/usr/bin/python3 /opt/mtproxy-pushok/scripts/promotion_agent.py
TimeoutStartSec=180
UMask=0077
UNIT
cat > /etc/systemd/system/mtproxy-pushok-promotion.timer <<'UNIT'
[Unit]
Description=Check Pushok placement schedule
[Timer]
OnBootSec=30s
OnUnitInactiveSec=20s
Unit=mtproxy-pushok-promotion.service
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now mtproxy-pushok-promotion.timer
