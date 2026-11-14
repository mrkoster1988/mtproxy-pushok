#!/usr/bin/env python3
"""Локальный агент systemd: применяет только hex-тег, никогда команды из БД."""
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
from staging_preflight import parse_env

ROOT = Path(__file__).resolve().parent.parent
ENV = Path('/etc/mtproxy-pushok/mtproxy.env')
STATE = Path('/etc/mtproxy-pushok/promotion-state.json')
SERVICE = 'mtproxy-pushok.service'
TAG = re.compile(r'[0-9a-f]{32}')


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
    line='MT_PROXY_TAG="'+tag+'"'
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
    if b'-P' in args:
        return args[args.index(b'-P')+1].decode()
    return ''


def apply(tag):
    text=ENV.read_text()
    cfg=parse_env(text)
    if cfg.get('MT_PROXY_TAG','')==tag and running_tag()==tag:
        return
    updated=tag_text(text,tag)
    atomic(ENV,updated)
    try:
        command('systemctl','restart',SERVICE)
        command('systemctl','is-active','--quiet',SERVICE)
        if running_tag()!=tag:
            raise RuntimeError('runner_did_not_apply_tag')
        with socket.create_connection(('127.0.0.1' if cfg['MT_PROXY_HOST']=='localhost' else cfg['MT_PROXY_HOST'],int(cfg['MT_PROXY_PORT'])),timeout=3):
            pass
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
    if plan.get('state') not in ('idle','apply') or not re.fullmatch(r'[0-9a-f]{64}',plan.get('revision','')):
        raise ValueError('invalid_plan')
    if plan['state']=='apply':
        if not TAG.fullmatch(plan.get('tag','')) or plan['tag']=='0'*32:
            raise ValueError('invalid_tag')
        if not time.time()<float(plan['expires_at'])<=time.time()+366*86400:
            raise ValueError('expired_plan')


def restore():
    if not STATE.exists(): return
    state=json.loads(STATE.read_text())
    apply(state['baseline'])
    STATE.unlink()


def tick():
    # Локальный срок снимает тег даже при недоступной БД/API.
    if STATE.exists() and float(json.loads(STATE.read_text())['expires_at'])<=time.time():
        restore()
    cfg=parse_env(ENV.read_text())
    request=dict(host=cfg['MT_PROXY_HOST'],port=int(cfg['MT_PROXY_PORT']))
    plan=exchange(request)
    validate_plan(plan)
    if plan['state']=='idle':
        restore()
    else:
        baseline=json.loads(STATE.read_text())['baseline'] if STATE.exists() else cfg.get('MT_PROXY_TAG','')
        # Сначала сохраняем исходный тег: восстановление возможно и после аварии.
        atomic(STATE,json.dumps(dict(baseline=baseline,expires_at=plan['expires_at'])))
        apply(plan['tag'])
    ack=exchange(dict(request,action='ack',revision=plan['revision']))
    if not ack.get('accepted'):
        restore()
        raise RuntimeError('plan_changed')


def main():
    if os.geteuid()!=0: raise RuntimeError('root_required')
    if not ENV.exists(): return
    info=ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise RuntimeError('unsafe_proxy_env')
    with open('/run/lock/pushok-promotion.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if '--restore' in __import__('sys').argv:
            restore(); return
        # Обновление кабинета может идти долго: истёкший тег снимается до ожидания.
        if STATE.exists() and float(json.loads(STATE.read_text())['expires_at'])<=time.time(): restore()
        from updater import lock_file
        with lock_file('host-lock') as update_lock:
            fcntl.flock(update_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            tick()

if __name__=='__main__':
    try:
        main()
    except BlockingIOError:
        pass
    except Exception as exc:
        # Не выводить stdout/stderr subprocess: в них могут быть конфигурация и тег.
        print('Автоприменение не завершено: '+type(exc).__name__+'. Повтор через 20 секунд.')
        raise SystemExit(1)
