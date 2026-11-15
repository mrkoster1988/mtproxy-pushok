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
from publish_proxy_link import publish_proxy_link


def register_proxy_node(root, host, public, port):
    # Сохраняем имя из env. Старую IP-запись с кампаниями и тегами оставляем.
    # Значения передаются psql как переменные, а не вставляются в SQL/оболочку.
    sql = """INSERT INTO proxy_nodes(name,host,port,status)
        SELECT 'Основной MTProxy', :'pushok_host', :'pushok_port'::integer, 'paused'
        WHERE NOT EXISTS (SELECT 1 FROM proxy_nodes
            WHERE host IN (:'pushok_host', :'pushok_ip') AND port=:'pushok_port'::integer)
        ON CONFLICT(host,port) DO NOTHING;"""
    run(root, ['exec','-T','db','sh','-c',
              'exec psql -X -v ON_ERROR_STOP=1 -v pushok_host="$1" -v pushok_ip="$2" -v pushok_port="$3" -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
              'pushok-register-node',host,public,str(port)],
        input=sql.encode(), stdout=subprocess.DEVNULL)


def main():
    root = Path(__file__).resolve().parent.parent
    env = Path('/etc/mtproxy-pushok/mtproxy.env')
    binary = Path('/opt/MTProxy/objs/bin/mtproto-proxy')
    runner = Path('/usr/local/sbin/mtproxy-pushok-run')
    if not env.exists() or not binary.exists() or not runner.exists():
        if not env.exists():
            publish_proxy_link(root, env)
        return
    cfg = parse_env(env.read_text())
    routes = json.loads(subprocess.check_output(['ip','-j','-4','route','get','1.1.1.1'], text=True))
    local = str(ipaddress.IPv4Address(routes[0]['prefsrc']))
    host = cfg['MT_PROXY_HOST'].strip().lower()
    public = str(ipaddress.IPv4Address(socket.gethostbyname(host)))
    if not ipaddress.ip_address(public).is_global:
        raise RuntimeError('Proxy host must resolve to a public IPv4')
    help_result = subprocess.run([str(binary), '--help'], capture_output=True, text=True, timeout=10)
    help_text = help_result.stdout + help_result.stderr
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
        try:
            subprocess.run(['systemctl','restart','mtproxy-pushok.service'],check=True)
            subprocess.run(['systemctl','is-active','--quiet','mtproxy-pushok.service'],check=True)
        except subprocess.CalledProcessError:
            # Ошибка запуска возвращает предыдущий runner, без смены секрета.
            shutil.copy2(backup, runner)
            subprocess.run(['systemctl','restart','mtproxy-pushok.service'],check=False)
            raise
    subprocess.run(['systemctl','is-active','--quiet','mtproxy-pushok.service'],check=True)
    publish_proxy_link(root, env)
    if (root / '.env').exists():
        port = int(cfg['MT_PROXY_PORT'])
        if not 1 <= port <= 65535:
            raise ValueError('Invalid proxy port')
        # Реестр не является подтверждением передачи сообщений. Новый узел на паузе.
        register_proxy_node(root,host,public,port)
    if root == Path('/opt/mtproxy-pushok') and (root / '.env').exists():
        subprocess.run(['bash', str(root / 'scripts/install_promotion_agent.sh')], check=True)
    print('MTProxy route configured; Telegram message delivery still requires a client check.')

if __name__ == '__main__':
    main()

