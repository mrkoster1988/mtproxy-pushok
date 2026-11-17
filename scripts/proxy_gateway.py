#!/usr/bin/env python3
"""Единый TCP-вход для native MTProxy на этом VPS, без разбора трафика."""
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import UUID

import proxy_provisioner as provisioner
from check_telegram_promo import load_proxy
from proxy_locks import runtime_lock, RuntimeBusy

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = Path('/var/lib/mtproxy-pushok-gateway')
CONFIG = DIRECTORY/'haproxy.cfg'
SERVICE = 'mtproxy-pushok-gateway.service'
CONTROL = ROOT/'.control'
HAPROXY = '/usr/sbin/haproxy'
RUNTIME_SOCKET = '/run/mtproxy-pushok-gateway/status.sock'


def ports(settings):
    port = settings['port']
    topics = settings['topics'] if settings['interests_enabled'] else []
    if type(port) is not int or not 1024 <= port <= 65000 or len(topics)>25:
        raise ValueError('invalid_settings')
    if len(set(topics)) != len(topics) or any(not re.fullmatch('[a-z_]{1,32}', t) for t in topics):
        raise ValueError('invalid_settings')
    result = {None:port, **{topic:port+i+1 for i,topic in enumerate(topics)}}
    if set(result.values()) & set(range(19999,20003)):
        raise ValueError('port_busy')
    return result


def configuration(settings, nodes, *, runtime_socket=RUNTIME_SOCKET):
    """Чистая генерация; все адреса/идентификаторы проверяются до записи."""
    listeners = ports(settings)
    if not re.fullmatch(r'/[a-zA-Z0-9_./-]{1,240}',runtime_socket) or '..' in Path(runtime_socket).parts:
        raise ValueError('invalid_socket')
    if not 1 <= len(nodes) <= 4:
        raise ValueError('no_nodes')
    seen = set()
    for n in nodes:
        if str(UUID(n['id'])) != n['id'] or n['id'] in seen:
            raise ValueError('invalid_node')
        seen.add(n['id'])
        ipaddress.IPv4Address(n['address'])
        if type(n['port']) is not int or not 1 <= n['port'] <= 65535 or n['port'] in listeners.values():
            raise ValueError('port_busy')
    lines = ['global','  nbthread 2','  maxconn 4096','  hard-stop-after 30m',
             f'  stats socket {runtime_socket} mode 600 level user',
             'defaults','  mode tcp','  timeout connect 3s','  timeout client 24h',
             '  timeout server 24h','  retries 2','  option redispatch','']
    for topic,port in listeners.items():
        name = topic or 'general'
        matches = {n['id'] for n in nodes if topic and n.get('topic')==topic}
        lines += [f'frontend in_{name}',f'  bind 0.0.0.0:{port}',f'  default_backend pool_{name}',
                  f'backend pool_{name}','  balance source','  hash-type consistent','  option allbackups']
        for n in sorted(nodes,key=lambda x:x['id']):
            backup = ' backup' if matches and n['id'] not in matches else ''
            lines.append(f"  server n_{n['id'].replace('-','')} {n['address']}:{n['port']} check inter 3s fall 2 rise 2{backup}")
        lines.append('')
    return '\n'.join(lines)


def atomic(path, text, *, mode=0o600, owner=None):
    fd,name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),mode)
            if owner is not None:
                os.fchown(stream.fileno(),owner,owner)
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def publish(name, data):
    atomic(CONTROL/name,json.dumps(data),owner=10001)


def exchange(hosts):
    result = subprocess.run([sys.executable,str(ROOT/'scripts/installer_runtime.py'),'compose','exec','-T',
        'api','python','-m','app.proxy_gateway'],cwd=ROOT,input=json.dumps({'hosts':hosts}),
        capture_output=True,text=True,timeout=20,check=True)
    return json.loads(result.stdout)


def process_ready(service, port, secret):
    try:
        pid = int(provisioner.command('systemctl','show',service,'--property=MainPID','--value'))
        if pid <= 1:
            return False
        args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
        secrets = [args[i+1].lower() for i,arg in enumerate(args[:-1]) if arg=='-S'
                   and re.fullmatch('[0-9a-fA-F]{32}', args[i+1])]
        return (Path(args[0]).name=='mtproto-proxy' and args[args.index('-H')+1]==str(port)
                and secret.lower() in secrets)
    except (OSError,ValueError,IndexError,subprocess.SubprocessError):
        return False


def candidates(settings, host, primary_port, secret, address):
    nodes = []
    for n in settings['nodes'][:16]:
        service = 'mtproxy-pushok.service'
        if n['port'] != primary_port:
            try:
                cfg = provisioner.read_instance(n['id'])
                if cfg['host'] != host or cfg['port'] != n['port']:
                    continue
                service = provisioner.unit(n['id'])
            except (OSError,ValueError,KeyError):
                continue
        if not process_ready(service,n['port'],secret):
            continue
        try:
            with socket.create_connection((address,n['port']),timeout=1):
                pass
        except OSError:
            continue
        if any(item['port']==n['port'] for item in nodes):
            continue
        nodes.append(dict(n,address=address))
    return nodes


def active():
    return subprocess.run(['systemctl','is-active','--quiet',SERVICE],capture_output=True,timeout=5).returncode==0


def previous_ports():
    try:
        return {int(p) for p in re.findall(r'^  bind 0\.0\.0\.0:(\d+)$',CONFIG.read_text(),re.M)}
    except FileNotFoundError:
        return set()


def worker_pid(path=RUNTIME_SOCKET):
    """USR2 асинхронен: новый PID рабочего процесса подтверждает принятие конфига."""
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(.5);client.connect(path);client.sendall(b'show info\n')
            data=b''
            while len(data)<16384:
                part=client.recv(min(4096,16384-len(data)))
                if not part:break
                data+=part
        match=re.search(rb'^Pid: ([0-9]+)$',data,re.M)
        return int(match[1]) if match else None
    except OSError:
        return None


def apply(settings, nodes):
    text = configuration(settings,nodes)
    DIRECTORY.mkdir(mode=0o755,parents=True,exist_ok=True)
    running = active()
    owned = previous_ports() if running else set()
    for port in ports(settings).values():
        if port not in owned and not provisioner.free_port(port):
            raise ValueError('port_busy')
    candidate = DIRECTORY/'candidate.cfg'
    atomic(candidate,text,mode=0o644)
    provisioner.command(HAPROXY,'-c','-f',str(candidate))
    previous = CONFIG.read_text() if CONFIG.exists() else None
    if text != previous or not running:
        # Не повторяем запись правил каждые 20 секунд; чужие правила не меняем.
        if shutil.which('ufw') and 'Status: active' in provisioner.command('ufw','status'):
            for port in ports(settings).values():
                provisioner.command('ufw','allow',str(port)+'/tcp','comment','Pushok gateway')
        old_pid=worker_pid() if running else None
        atomic(CONFIG,text,mode=0o644)
        try:
            provisioner.command('systemctl','reload' if running else 'start',SERVICE)
            deadline=time.monotonic()+4
            while True:
                new_pid=worker_pid()
                if new_pid and new_pid!=old_pid:break
                if time.monotonic()>=deadline:raise RuntimeError('start_failed')
                time.sleep(.1)
            if not active():
                raise RuntimeError('start_failed')
            # Проверяем listeners после reload, не объявляем успешным только exit code.
            for port in ports(settings).values():
                deadline=time.monotonic()+3
                while True:
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.5):
                            break
                    except OSError:
                        if time.monotonic()>=deadline:
                            raise
                        time.sleep(.1)
        except Exception:
            if previous is not None:
                atomic(CONFIG,previous,mode=0o644)
                subprocess.run(['systemctl','reload' if running else 'stop',SERVICE],capture_output=True,timeout=10)
            else:
                subprocess.run(['systemctl','stop',SERVICE],capture_output=True,timeout=10)
            raise
    candidate.unlink(missing_ok=True)


def tick(status):
    host, primary_port, secret = load_proxy(provisioner.BASE/'mtproxy.env')
    host=host.lower()
    public = provisioner.primary_host()
    status['hosts'] = list(dict.fromkeys([host,public]))
    settings = exchange(status['hosts'])
    status.update(revision=settings['revision'],port=settings['port'],interests_enabled=settings['interests_enabled'])
    if not settings['enabled']:
        (CONTROL/'proxy-gateway.json').unlink(missing_ok=True)
        provisioner.command('systemctl','stop',SERVICE)
        status['code']='disabled'; return
    if not Path(HAPROXY).is_file():
        raise RuntimeError('dependency_missing')
    route = json.loads(provisioner.command('ip','-j','-4','route','get','1.1.1.1'))
    address = str(ipaddress.IPv4Address(route[0]['prefsrc']))
    nodes = candidates(settings,public,primary_port,secret,address)
    status.update(nodes=len(nodes),excluded=len(settings['nodes'])-len(nodes))
    if not nodes:
        provisioner.command('systemctl','stop',SERVICE)
        raise RuntimeError('no_nodes')
    apply(settings,nodes)
    from publish_proxy_link import connection_url
    publish('proxy-gateway.json',dict(url=connection_url(host,settings['port'],secret),
        expires_at=time.time()+120,revision=settings['revision'],
        topic_ports={k:v for k,v in ports(settings).items() if k}))
    status['code']='ready'


def main():
    if os.geteuid()!=0:
        return 1
    status = {'code':'check_failed'}
    try:
        with runtime_lock():
            tick(status)
    except RuntimeBusy:
        # Короткое обслуживание не затирает последний успешный отчёт; TTL ограничен.
        return 0
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as error:
        code = str(error)
        status['code'] = code if code in ('port_busy','no_nodes','dependency_missing') else 'check_failed'
        (CONTROL/'proxy-gateway.json').unlink(missing_ok=True)
    status['checked_at']=time.time()
    publish('proxy-gateway-status.json',status)
    print(json.dumps(status))
    return 0 if status['code'] in ('ready','disabled') else 1


if __name__=='__main__':
    sys.exit(main())
