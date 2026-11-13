#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Запустите от root'; exit 1; }
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
[[ "$ROOT" == /opt/mtproxy-pushok ]] || { echo 'Служба рассчитана на /opt/mtproxy-pushok'; exit 1; }
install -d -o 10001 -g 10001 -m 0700 .control
cat > /etc/systemd/system/mtproxy-pushok-updater.service <<'UNIT'
[Unit]
Description=Pushok published release updater
After=docker.service network-online.target
[Service]
Type=oneshot
WorkingDirectory=/opt/mtproxy-pushok
ExecStart=/usr/bin/python3 /opt/mtproxy-pushok/scripts/updater.py
ExecStopPost=/usr/bin/python3 /opt/mtproxy-pushok/scripts/updater.py --finalize
TimeoutStartSec=3600
UMask=0077
UNIT
cat > /etc/systemd/system/mtproxy-pushok-updater.path <<'UNIT'
[Unit]
Description=Watch Pushok update requests
[Path]
PathExists=/opt/mtproxy-pushok/.control/request.json
Unit=mtproxy-pushok-updater.service
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
printf '{"version":1}\n' > .control/installed.json
chown 10001:10001 .control/installed.json
chmod 0600 .control/installed.json
systemctl enable --now mtproxy-pushok-updater.path
if [[ "${1:-}" != --install-only ]]; then
  args=(--bootstrap)
  [[ "${1:-}" != --enable-stars ]] || args+=(--enable-stars)
  python3 scripts/updater.py "${args[@]}"
fi
