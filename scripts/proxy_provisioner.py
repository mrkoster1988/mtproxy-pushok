#!/usr/bin/env python3
"""Root-агент: только дополнительные native MTProxy на текущем VPS, без shell из ИИ."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
from uuid import UUID

from staging_preflight import parse_env
from promotion_agent import atomic

ROOT = Path(__file__).resolve().parent.parent
BASE = Path('/etc/mtproxy-pushok')
INSTANCES = BASE / 'instances'
BINARY = Path('/opt/MTProxy/objs/bin/mtproto-proxy')
UNITS = Path('/etc/systemd/system')


class ProvisionError(RuntimeError):
    def __init__(self, code, retry_seconds=900):
        super().__init__(code)
        ceiling = 2147483647 if code=='telegram_flood_wait' else 604800
        self.retry_seconds = max(900,min(ceiling,retry_seconds)) if type(retry_seconds) is int else 900


def identity(value):
    if str(UUID(value)) != value:
        raise ValueError('invalid_instance')
    return value


def unit(node_id):
    return 'mtproxy-pushok-auto-'+identity(node_id)+'.service'


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=45).stdout.strip()


def private_read(path):
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size>8192:
            raise ValueError('unsafe_config')
        return stream.read(8193)


def primary_alias():
    cfg = parse_env(private_read(BASE/'mtproxy.env'))
    return cfg['MT_PROXY_HOST'].lower()


def primary_host():
    host = str(ipaddress.IPv4Address(socket.gethostbyname(primary_alias())))
    if not ipaddress.ip_address(host).is_global:
        raise ValueError('invalid_host')
    return host


def resource_ready():
    memory = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0])
    return (available >= 768*1024 and shutil.disk_usage(BASE).free >= 256*1024*1024
            and os.getloadavg()[0] < max(1,os.cpu_count() or 1)*0.8)


def free_port(port):
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            sock.bind(('0.0.0.0',port))
        return True
    except OSError:
        return False


def exchange(data):
    def execute():
        result = subprocess.run([sys.executable,str(ROOT/'scripts/installer_runtime.py'),'compose','exec','-T',
            'api','python','-m','app.proxy_provisioning'],cwd=ROOT,input=json.dumps(data),
            text=True,capture_output=True,timeout=45,check=True)
        return json.loads(result.stdout)
    if data.get('action') == 'inspect':
        from updater import lock_file
        # Обновлятор заменяет код и контейнеры под этой же блокировкой.
        # Не объявляем его штатную работу потерей связи с кабинетом.
        with lock_file('host-lock') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return execute()
    return execute()


def read_instance(node_id):
    cfg = json.loads(private_read(INSTANCES/identity(node_id)/'instance.json'))
    if cfg.get('node_id') != node_id or cfg.get('host') != primary_host():
        raise ValueError('invalid_instance')
    if type(cfg.get('port')) is not int or not 1024<=cfg['port']<=65535:
        raise ValueError('invalid_instance')
    if type(cfg.get('stats_port')) is not int or not 20000<=cfg['stats_port']<=20002:
        raise ValueError('invalid_instance')
    import re
    if not isinstance(cfg.get('secret'),str) or not re.fullmatch('[a-f0-9]{32}',cfg['secret']):
        raise ValueError('invalid_instance')
    return cfg


def create_instance(job):
    node_id = identity(job['node_id'])
    if job['host'] != primary_host() or not resource_ready():
        raise RuntimeError('resources_low')
    INSTANCES.mkdir(mode=0o700,parents=True,exist_ok=True)
    if INSTANCES.is_symlink() or INSTANCES.stat().st_uid != 0 or INSTANCES.stat().st_mode & 0o077:
        raise ValueError('unsafe_config')
    directory = INSTANCES/node_id
    if directory.exists():
        cfg = read_instance(node_id)
        if (cfg['host'],cfg['port']) != (job['host'],job['port']):
            raise ValueError('invalid_instance')
        return cfg
    existing = [p for p in INSTANCES.iterdir() if p.is_dir() and not p.name.startswith('.provision-')]
    if len(existing)>=3:
        raise RuntimeError('resources_low')
    port = job['port']
    if type(port) is not int or not 1024<=port<=65535 or port in range(20000,20003) or not free_port(port):
        raise RuntimeError('port_busy')
    used = {read_instance(p.name)['stats_port'] for p in existing}
    stats_port = next((p for p in range(20000,20003) if p not in used and free_port(p)),None)
    if stats_port is None:
        raise RuntimeError('port_busy')
    cfg = dict(node_id=node_id,host=job['host'],port=port,stats_port=stats_port,secret=secrets.token_hex(16))
    import tempfile
    with tempfile.TemporaryDirectory(prefix='.provision-',dir=INSTANCES) as temp:
        staged = Path(temp)
        atomic(staged/'instance.json',json.dumps(cfg))
        atomic(staged/'mtproxy.env',f"MT_PROXY_HOST={cfg['host']}\nMT_PROXY_PORT={port}\nMT_PROXY_SECRET={cfg['secret']}\nMT_PROXY_TAG=\n")
        staged.rename(directory)
    return cfg


def write_unit(name, value):
    path = UNITS/name
    if path.exists() and path.read_text() != value:
        raise RuntimeError('agent_setup_failed')
    if path.is_symlink():
        raise RuntimeError('agent_setup_failed')
    atomic(path,value)
    path.chmod(0o644)


def install_instance(cfg):
    node_id = cfg['node_id']
    if not BINARY.is_file() or ROOT != Path('/opt/mtproxy-pushok'):
        raise RuntimeError('host_unavailable')
    write_unit(unit(node_id),f'''# Managed by Pushok proxy provisioner
[Unit]
Description=Pushok additional MTProxy {node_id}
After=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/mtproxy-pushok/scripts/proxy_provisioner.py run {node_id}
Restart=on-failure
RestartSec=5
LimitNOFILE=16384
MemoryMax=384M
CPUQuota=50%
TasksMax=64
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/mtproxy-pushok
[Install]
WantedBy=multi-user.target
''')
    write_unit('mtproxy-pushok-auto-agent@.service','''# Managed by Pushok proxy provisioner
[Unit]
Description=Pushok additional proxy promotion agent
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/mtproxy-pushok/scripts/promotion_agent.py --instance %i
WorkingDirectory=/opt/mtproxy-pushok
TimeoutStartSec=300
UMask=0077
''')
    write_unit('mtproxy-pushok-auto-agent@.timer','''# Managed by Pushok proxy provisioner
[Unit]
Description=Pushok additional proxy promotion timer
[Timer]
OnBootSec=60
OnUnitInactiveSec=30
AccuracySec=1
[Install]
WantedBy=timers.target
''')
    # Не меняем правила внешнего firewall провайдера. UFW открывает только новый порт.
    if shutil.which('ufw') and 'Status: active' in command('ufw','status'):
        command('ufw','allow',str(cfg['port'])+'/tcp','comment','Pushok auto '+node_id)
    command('systemctl','daemon-reload')
    command('systemctl','enable','--now',unit(node_id))


def ready(cfg):
    try:
        command('systemctl','is-active','--quiet',unit(cfg['node_id']))
        pid = int(command('systemctl','show',unit(cfg['node_id']),'--property=MainPID','--value'))
        if pid<=1:
            return False
        args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
        if Path(args[0]).name != 'mtproto-proxy' or args[args.index('-H')+1] != str(cfg['port']) or args[args.index('-S')+1] != cfg['secret']:
            return False
        with socket.create_connection((cfg['host'],cfg['port']),timeout=2):
            return True
    except (OSError,ValueError,IndexError,subprocess.SubprocessError):
        return False


def stop_instance(node_id):
    identity(node_id)
    names = ('mtproxy-pushok-auto-agent@'+node_id+'.timer',unit(node_id),
             'mtproxy-pushok-auto-agent@'+node_id+'.service')
    for name in names:
        subprocess.run(['systemctl','disable','--now',name],capture_output=True,timeout=45)
    for name in names:
        result = subprocess.run(['systemctl','show',name,'--property=LoadState,ActiveState'],
                                capture_output=True,text=True,timeout=45)
        values = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        missing = values.get('LoadState')=='not-found' and result.returncode in (0,1)
        if (result.returncode!=0 and not missing) or values.get('ActiveState') not in ('inactive','failed'):
            raise RuntimeError('start_failed')
    (ROOT/'.control'/('proxy-link-'+node_id+'.json')).unlink(missing_ok=True)


def receipt_path(node_id):
    return INSTANCES/identity(node_id)/'completion.json'


def save_receipt(job, tag):
    path = receipt_path(job['node_id'])
    atomic(path,json.dumps({'job_id':identity(job['job_id']),'tag':tag}))
    fd = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepared(cfg):
    if not ready(cfg):
        return False
    try:
        command('systemctl','is-active','--quiet','mtproxy-pushok-auto-agent@'+cfg['node_id']+'.timer')
        return True
    except (OSError,subprocess.SubprocessError):
        return False


def reconcile(host):
    """Под общей блокировкой хоста завершаем ACK или убираем только не принятый БД узел."""
    if not INSTANCES.exists():
        return
    if INSTANCES.is_symlink() or INSTANCES.stat().st_uid!=0 or INSTANCES.stat().st_mode & 0o077:
        raise ValueError('unsafe_config')
    for directory in sorted(INSTANCES.iterdir()):
        if directory.name.startswith('.provision-'):
            continue
        cfg = read_instance(identity(directory.name))
        result = exchange(dict(action='recover',host=host,node_id=cfg['node_id'],port=cfg['port']))
        path = receipt_path(cfg['node_id'])
        if result.get('state')=='active':
            # Даже если настройки уже выключены, принятый БД экземпляр сохраняется.
            path.unlink(missing_ok=True)
            continue
        if result.get('state') not in ('pending','blocked'):
            raise ProvisionError('recovery_pending')  # Нет подтверждения — не останавливаем.
        job_id = identity(result['job_id'])
        if result['state']=='pending' and path.exists() and prepared(cfg):
            receipt = json.loads(private_read(path))
            import re
            if (set(receipt)!={'job_id','tag'} or receipt['job_id']!=job_id
                    or not isinstance(receipt['tag'],str) or not re.fullmatch('[a-f0-9]{32}',receipt['tag'])
                    or receipt['tag']=='0'*32):
                raise ValueError('invalid_receipt')
            completed = exchange(dict(action='complete',host=host,job_id=job_id,tag=receipt['tag'],ready=True))
            if not completed.get('accepted'):
                if completed.get('reason') in ('registration_conflict','tag_not_verified'):
                    stop_instance(cfg['node_id'])
                    path.unlink(missing_ok=True)
                    if not exchange(dict(action='failed',host=host,job_id=job_id,code=completed['reason'])).get('accepted'):
                        raise ProvisionError('recovery_pending')
                    continue
                raise ProvisionError('recovery_pending')
            path.unlink(missing_ok=True)
            continue
        # После kill/reboot до финального ACK не оставляем незарегистрированную службу.
        stop_instance(cfg['node_id'])
        path.unlink(missing_ok=True)
        if not exchange(dict(action='interrupted',host=host,job_id=job_id)).get('accepted'):
            raise ProvisionError('recovery_pending')


def provision(job):
    from automatic_binding import helper
    cfg = create_instance(job)
    def authorize():
        if not exchange(dict(action='authorize',host=cfg['host'],job_id=job['job_id'])).get('accepted'):
            raise RuntimeError('policy_changed')
    try:
        authorize()
        install_instance(cfg)
        for _ in range(15):
            if ready(cfg):
                break
            time.sleep(1)
        else:
            raise RuntimeError('start_failed')
        authorize()
        reply = helper(ROOT,dict(action='register',node_id=cfg['node_id'],host=cfg['host'],port=cfg['port'],secret=cfg['secret']))
        if not reply.get('ok'):
            raise ProvisionError(reply.get('code','telegram_unavailable'),reply.get('retry_seconds',900))
        import re
        if not isinstance(reply.get('tag'),str) or not re.fullmatch('[a-f0-9]{32}',reply['tag']) or reply['tag']=='0'*32:
            raise RuntimeError('tag_not_verified')
        # Агент умеет работать с paused-узлом. До подтверждения БД ссылка не публикуется.
        authorize()
        command('systemctl','enable','--now','mtproxy-pushok-auto-agent@'+cfg['node_id']+'.timer')
        if not ready(cfg):
            raise RuntimeError('start_failed')
        save_receipt(job,reply['tag'])
    except Exception:
        stop_instance(cfg['node_id'])
        raise
    # Не останавливаем готовую службу при потере ответа: БД могла принять ACK.
    result = exchange(dict(action='complete',host=cfg['host'],job_id=job['job_id'],tag=reply['tag'],ready=True))
    if not result.get('accepted'):
        raise ProvisionError('recovery_pending')
    receipt_path(cfg['node_id']).unlink(missing_ok=True)


def tick(status=None):
    status = status if status is not None else {}
    from automatic_binding import connection_status
    from proxy_locks import runtime_lock
    if os.geteuid()!=0:
        raise RuntimeError('root_required')
    if not (BASE/'mtproxy.env').exists():
        status['code'] = 'host_unavailable'
        return
    host = primary_host()
    with open('/run/lock/pushok-provisioner.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with runtime_lock(wait_seconds=30):
            reconcile(host)
            cfg = exchange(dict(action='settings',host=host))
            status['policy_revision'] = cfg.get('policy_revision')
            if cfg.get('state')=='wait':
                status['code'] = 'agent_busy'
                return
            if cfg.get('enabled') is not True:
                status['code'] = 'disabled'
                return
            if (type(cfg.get('first_port')) is not int or not 1024<=cfg['first_port']<=65533
                    or type(cfg.get('max_instances')) is not int or not 1<=cfg['max_instances']<=3):
                raise ValueError('invalid_host_policy')
            owner = connection_status()
            if (not owner['enabled'] or not owner['helper_available'] or not owner['session_saved']
                    or owner['state'] not in ('connected','session_saved','unavailable')):
                status['code'] = 'owner_unavailable'
                return
            if owner.get('retry_seconds',0)>0:
                from datetime import datetime, timedelta, timezone
                status.update(code='telegram_flood_wait',retry_at=(datetime.now(timezone.utc)+timedelta(seconds=owner['retry_seconds'])).isoformat())
                return
            if not resource_ready():
                status['code'] = 'resources_low'
                return
            from proxy_readiness import native_ready, timers_ready
            if not native_ready(BASE, BINARY):
                status['code'] = 'native_unavailable'
                return
            if not timers_ready():
                status['code'] = 'timer_unavailable'
                return
            ports = [p for p in range(cfg['first_port'],cfg['first_port']+cfg['max_instances']) if free_port(p)]
            primary = parse_env(private_read(BASE/'mtproxy.env'))['MT_PROXY_HOST'].lower()
            job = exchange(dict(action='claim',host=host,primary_host=primary,resources_ok=True,free_ports=ports))
            if job.get('state')!='provision':
                status['code'] = job.get('reason','waiting')
                return
            status['code'] = 'provisioning'
            publish_report(status)
            try:
                provision(job)
                status['code'] = 'active'
            except Exception as exc:
                if receipt_path(job['node_id']).exists():
                    # После потери ACK не записываем ложную ошибку/исчерпание попыток.
                    status['code'] = 'recovery_pending'
                    return
                code = str(exc) if isinstance(exc,RuntimeError) else 'host_unavailable'
                exchange(dict(action='failed',host=host,job_id=job['job_id'],code=code,
                              retry_seconds=getattr(exc,'retry_seconds',900)))
                status['code'] = code


def publish_report(status):
    from datetime import datetime, timezone
    import re
    code = status.get('code','host_unavailable')
    if not isinstance(code,str) or not re.fullmatch('[a-z_]{1,48}',code):
        code = 'host_unavailable'
    path = ROOT/'.control'/'proxy-provisioner.json'
    try:
        report = dict(code=code,checked_at=datetime.now(timezone.utc).isoformat())
        revision = status.get('policy_revision')
        if isinstance(revision,str) and re.fullmatch('[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}',revision):
            report['policy_revision'] = revision
        if isinstance(status.get('retry_at'),str):
            report['retry_at'] = status['retry_at']
        atomic(path,json.dumps(report))
        os.chown(path,10001,10001)
    except OSError:
        pass


def run_instance(node_id):
    cfg = read_instance(node_id)
    env = parse_env(private_read(INSTANCES/node_id/'mtproxy.env'))
    if (env.get('MT_PROXY_HOST'),env.get('MT_PROXY_PORT'),env.get('MT_PROXY_SECRET')) != (cfg['host'],str(cfg['port']),cfg['secret']):
        raise ValueError('invalid_instance')
    route = json.loads(command('ip','-j','-4','route','get','1.1.1.1'))
    local = str(ipaddress.IPv4Address(route[0]['prefsrc']))
    args = [str(BINARY),'-u','nobody','-p',str(cfg['stats_port']),'-H',str(cfg['port']),'-S',cfg['secret'],
            '--address',local,'--nat-info',local+':'+cfg['host'],
            '--aes-pwd',str(BASE/'proxy-secret'),str(BASE/'proxy-multi.conf'),'-M','1']
    tag = env.get('MT_PROXY_TAG','')
    if tag:
        import re
        if not re.fullmatch('[a-f0-9]{32}',tag) or tag=='0'*32:
            raise ValueError('invalid_tag')
        args.extend(['-P',tag])
    os.execv(str(BINARY),args)


def main():
    status = {'code':'host_unavailable'}
    try:
        if len(sys.argv)==2 and sys.argv[1]=='check':
            if os.geteuid()!=0:
                raise RuntimeError('root_required')
            from proxy_readiness import inspect, publish
            report = inspect(sys.modules[__name__], source='manual')
            publish(sys.modules[__name__], report)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if all(value=='ready' for value in report['checks'].values()) else 2
        elif len(sys.argv)==3 and sys.argv[1]=='run':
            run_instance(identity(sys.argv[2]))
        elif len(sys.argv)==1:
            tick(status)
        else:
            raise ValueError('invalid_arguments')
    except BlockingIOError:
        status['code'] = 'agent_busy'
    except ProvisionError as exc:
        status['code'] = str(exc)
    except Exception as exc:
        # Никогда не выводим argv процесса, secret или диалог Telegram.
        print('proxy_provisioner_failed: '+type(exc).__name__,file=sys.stderr)
        return 1
    finally:
        if len(sys.argv)==1:
            publish_report(status)
            if os.geteuid()==0:
                try:
                    from proxy_readiness import inspect, publish
                    publish(sys.modules[__name__], inspect(sys.modules[__name__]))
                except Exception:
                    # Не подменяем последний отчёт ложной готовностью при сбое проверки.
                    print('proxy_readiness_failed', file=sys.stderr)
    return 0


if __name__=='__main__':
    sys.exit(main())
