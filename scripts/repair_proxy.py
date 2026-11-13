#!/usr/bin/env python3
"""Выбор исходящего IPv4 вместо случайного интерфейса, без смены секрета."""
import ipaddress
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from staging_preflight import parse_env
from installer_runtime import run

def main():
    env = Path('/etc/mtproxy-pushok/mtproxy.env')
    binary = Path('/opt/MTProxy/objs/bin/mtproto-proxy')
    runner = Path('/usr/local/sbin/mtproxy-pushok-run')
    if not env.exists() or not binary.exists() or not runner.exists():
        return
    cfg = parse_env(env.read_text())
    routes = json.loads(subprocess.check_output(['ip','-j','-4','route','get','1.1.1.1'], text=True))
    local = str(ipaddress.IPv4Address(routes[0]['prefsrc']))
    host = cfg['MT_PROXY_HOST']
    public = str(ipaddress.IPv4Address(socket.gethostbyname(host)))
    if not ipaddress.ip_address(public).is_global:
        raise RuntimeError('Proxy host must resolve to a public IPv4')
    help_text = subprocess.run([str(binary), '--help'], capture_output=True, text=True).stderr
    if '--address' not in help_text or '--nat-info' not in help_text:
        raise RuntimeError('Proxy build lacks address options')
    source = runner.read_text()
    marker = 'set -- -u nobody -p 8888 -H "$MT_PROXY_PORT" -S "$MT_PROXY_SECRET"'
    if '# pushok explicit route' not in source:
        if marker not in source:
            raise RuntimeError('Custom proxy runner; not overwritten')
        backup = runner.with_suffix('.before-v051')
        if not backup.exists():
            shutil.copy2(runner, backup)
        source = source.replace(marker, marker + '\n# pushok explicit route\nset -- "$@" --address '+local+' --nat-info '+local+':'+public)
        tmp = runner.with_suffix('.new')
        tmp.write_text(source);tmp.chmod(0o755);tmp.replace(runner)
        subprocess.run(['systemctl','restart','mtproxy-pushok.service'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','mtproxy-pushok.service'],check=True)
    root = Path(__file__).resolve().parent.parent
    if (root / '.env').exists():
        port = int(cfg['MT_PROXY_PORT'])
        if not 1 <= port <= 65535:
            raise ValueError('Invalid proxy port')
        # Реестр не является подтверждением передачи сообщений. Новый узел на паузе.
        sql = "INSERT INTO proxy_nodes(name,host,port,status) VALUES ('Основной MTProxy','%s',%d,'paused') ON CONFLICT(host,port) DO NOTHING;" % (public, port)
        run(root, ['exec','-T','db','sh','-c','exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], input=sql.encode(), stdout=subprocess.DEVNULL)
    print('MTProxy route configured; Telegram message delivery still requires a client check.')

if __name__ == '__main__':
    main()
