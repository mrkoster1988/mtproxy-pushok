#!/usr/bin/env python3
"""Локальный агент systemd: применяет только hex-тег, никогда команды из БД."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from staging_preflight import parse_env

ROOT = Path(__file__).resolve().parent.parent
ENV = Path('/etc/mtproxy-pushok/mtproxy.env')
STATE = Path('/etc/mtproxy-pushok/promotion-state.json')
STATUS = ROOT / '.control' / 'promotion-agent.json'
SERVICE = 'mtproxy-pushok.service'
TAG = re.compile(r'[0-9a-fA-F]{32}')
PLAN_CODES = {
    'ready', 'idle', 'default_sponsor', 'default_binding_missing', 'node_not_found', 'node_ambiguous', 'node_paused', 'node_archived',
    'engine_unsupported', 'manual_placement_active', 'conflicting_campaigns',
    'binding_missing', 'binding_changed', 'channel_archived', 'moderation_pending',
    'payment_pending', 'waiting_start', 'period_ended', 'no_current_campaign',
}
REPORT_CODES = PLAN_CODES | {
    'tag_applied', 'waiting_update', 'agent_busy', 'env_missing', 'config_error',
    'unsafe_config', 'permission_error', 'runtime_unavailable', 'invalid_plan',
    'apply_failed', 'restore_failed', 'ack_unavailable', 'plan_changed', 'restored', 'agent_error',
}


def publish_status(status):
    """Пишем только короткие коды; недоступность отчёта не мешает откату тега."""
    code = status.get('code')
    report = dict(code=code if code in REPORT_CODES else 'agent_error',
                  checked_at=datetime.now(timezone.utc).isoformat())
    host, port = status.get('host'), status.get('port')
    if isinstance(host, str) and re.fullmatch(r'[a-zA-Z0-9.:-]{1,253}', host):
        report['host'] = host
    if type(port) is int and 1 <= port <= 65535:
        report['port'] = port
    for key, pattern in [('node_id', r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'),
                         ('plan_revision', r'[0-9a-f]{64}')]:
        value = status.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            report[key] = value
    name = None
    try:
        fd, name = tempfile.mkstemp(dir=STATUS.parent)
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), 10001, 10001)
            json.dump(report, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, STATUS)
    except OSError:
        pass
    finally:
        if name:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass


def atomic(path, text):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def tag_text(text, tag):
    if tag and not TAG.fullmatch(tag):
        raise ValueError('invalid_tag')
    # Только проверенный hex: формат совместим с old_value() установщика.
    line='MT_PROXY_TAG='+tag
    rows=text.splitlines()
    matches=[i for i,s in enumerate(rows) if re.match(r'^\s*(?:export\s+)?MT_PROXY_TAG=',s)]
    if len(matches)>1:
        raise ValueError('duplicate_tag')
    if matches: rows[matches[0]]=line
    else: rows.append(line)
    return '\n'.join(rows)+'\n'


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=45).stdout.strip()


def running_tag():
    pid=int(command('systemctl','show',SERVICE,'-p','MainPID','--value'))
    args=Path('/proc',str(pid),'cmdline').read_bytes().split(b'\0')
    if not args or Path(os.fsdecode(args[0])).name != 'mtproto-proxy':
        raise RuntimeError('proxy_not_started')
    if b'-P' in args:
        return args[args.index(b'-P')+1].decode()
    return ''


def wait_ready(tag, cfg, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        try:
            command('systemctl','is-active','--quiet',SERVICE)
            if running_tag().lower() != tag.lower():
                raise RuntimeError('runner_did_not_apply_tag')
            host = '127.0.0.1' if cfg['MT_PROXY_HOST']=='localhost' else cfg['MT_PROXY_HOST']
            with socket.create_connection((host,int(cfg['MT_PROXY_PORT'])),timeout=1):
                return
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError('proxy_start_timeout') from exc
            time.sleep(0.25)


def apply(tag):
    text=ENV.read_text()
    cfg=parse_env(text)
    try:
        matches = running_tag().lower()==tag.lower()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        matches = False
    if cfg.get('MT_PROXY_TAG','').lower()==tag.lower() and matches:
        try:
            # Совпадение аргумента -P не доказывает, что служба принимает соединения.
            wait_ready(tag, cfg)
            return
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pass  # Недоступную службу пробуем восстановить тем же перезапуском.
    updated=tag_text(text,tag)
    atomic(ENV,updated)
    try:
        command('systemctl','restart',SERVICE)
        wait_ready(tag, cfg)
    except Exception:
        atomic(ENV,text)
        command('systemctl','restart',SERVICE)
        raise


def exchange(request):
    proc=subprocess.run(['python3','scripts/installer_runtime.py','compose','exec','-T','api',
                         'python','-m','app.promotion_runtime'],cwd=ROOT,input=json.dumps(request),
                        text=True,capture_output=True,check=True,timeout=20)
    return json.loads(proc.stdout)


def validate_plan(plan):
    if plan.get('state') not in ('idle','apply','default') or not re.fullmatch(r'[0-9a-f]{64}',plan.get('revision','')):
        raise ValueError('invalid_plan')
    if plan['state'] in ('apply','default'):
        if not TAG.fullmatch(plan.get('tag','')) or plan['tag']=='0'*32:
            raise ValueError('invalid_tag')
        if plan['state']=='apply' and not time.time()<float(plan['expires_at'])<=time.time()+366*86400:
            raise ValueError('expired_plan')


def restore():
    if not STATE.exists(): return False
    state=json.loads(STATE.read_text())
    if state.get('restore_required') is not True:
        state['restore_required'] = True
        atomic(STATE, json.dumps(state))
    apply(state['baseline'])
    STATE.unlink()
    return True


def restore_pending_or_expired():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    if state.get('restore_required') is True or float(state['expires_at']) <= time.time():
        restore()


def node_request(cfg):
    """Старый repair регистрировал IPv4 вместо домена из конфигурации."""
    host = cfg['MT_PROXY_HOST'].strip().lower()
    request = dict(host=host, port=int(cfg['MT_PROXY_PORT']))
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            request['resolved_host'] = str(ipaddress.IPv4Address(socket.gethostbyname(host)))
        except (OSError, ValueError):
            # Точное доменное имя всё ещё может совпадать с записью в реестре.
            pass
    return request


def tick(status=None):
    status = status if status is not None else {}
    # Локальный срок снимает тег даже при недоступной БД/API.
    status['code'] = 'restore_failed'
    restore_pending_or_expired()
    status['code'] = 'config_error'
    cfg=parse_env(ENV.read_text())
    request=node_request(cfg)
    status.update(host=request['host'], port=request['port'], code='runtime_unavailable')
    plan=exchange(request)
    status['code'] = 'invalid_plan'
    validate_plan(plan)
    status.update(node_id=plan.get('node_id'), plan_revision=plan['revision'])
    if plan['state']=='idle':
        status['code'] = 'restore_failed'
        restore()
    else:
        status['code'] = 'apply_failed'
        baseline=json.loads(STATE.read_text())['baseline'] if STATE.exists() else cfg.get('MT_PROXY_TAG','')
        # Незавершённая попытка требует возврата к исходному тегу, даже если
        # новый слот ещё не истёк, кабинет недоступен или идёт обновление.
        saved = dict(baseline=baseline, expires_at=plan.get('expires_at',253402214400), restore_required=True)
        atomic(STATE, json.dumps(saved))
        try:
            apply(plan['tag'])
            saved['restore_required'] = False
            atomic(STATE, json.dumps(saved))
        except Exception:
            # apply() возвращает предыдущий env, в котором может быть тег
            # уже отменённой кампании. Завершаем возврат к исходному тегу.
            status['code'] = 'restore_failed'
            restore()
            status['code'] = 'apply_failed'
            raise
    status['code'] = 'ack_unavailable'
    ack=exchange(dict(request,action='ack',revision=plan['revision']))
    if not ack.get('accepted'):
        status['code'] = 'restore_failed'
        restore()
        status['code'] = 'plan_changed'
        raise RuntimeError('plan_changed')
    reason = plan.get('reason', 'idle')
    status['code'] = 'tag_applied' if plan['state'] in ('apply','default') else reason if reason in PLAN_CODES else 'agent_error'


def main(status=None):
    status = status if status is not None else {}
    status['code'] = 'permission_error'
    if os.geteuid()!=0: raise RuntimeError('root_required')
    status['code'] = 'env_missing'
    if not ENV.exists(): return
    status['code'] = 'unsafe_config'
    info=ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise RuntimeError('unsafe_proxy_env')
    with open('/run/lock/pushok-promotion.lock','w') as lock:
        status['code'] = 'agent_busy'
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if '--restore' in __import__('sys').argv:
            status['code'] = 'restore_failed'
            status['code'] = 'restored' if restore() else 'idle'
            return
        # Обновление кабинета может идти долго: истёкший тег снимается до ожидания.
        status['code'] = 'restore_failed'
        restore_pending_or_expired()
        from updater import lock_file
        status['code'] = 'waiting_update'
        with lock_file('host-lock') as update_lock:
            fcntl.flock(update_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            tick(status)


def run_cycle():
    status = {'code': 'agent_error'}
    try:
        main(status)
    finally:
        publish_status(status)

if __name__=='__main__':
    try:
        run_cycle()
    except BlockingIOError:
        pass
    except Exception as exc:
        # Не выводить stdout/stderr subprocess: в них могут быть конфигурация и тег.
        print('Автоприменение не завершено: '+type(exc).__name__+'. Повтор через 20 секунд.')
        raise SystemExit(1)

