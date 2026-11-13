#!/usr/bin/env python3
"""Host-only worker: fixed published release ref, private backup, build and health check."""
import json
import re
from contextlib import contextmanager
import tempfile
import os
import subprocess
import sys
import fcntl
import stat
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / '.control'
REMOTE = 'https://github.com/mrkoster1988/mtproxy-pushok.git'

def run(*args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=1800).stdout

def write_control(filename, data):
    fd, name = tempfile.mkstemp(dir=CONTROL)
    with os.fdopen(fd, 'w') as stream:
        os.fchown(stream.fileno(), 10001, 10001)
        json.dump(data, stream)
    Path(name).replace(CONTROL / filename)

def status(state, **fields):
    write_control('status.json', dict(state=state, at=datetime.now(timezone.utc).isoformat(), **fields))

@contextmanager
def lock_file(name):
    fd = os.open(CONTROL / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError('Invalid lock file')
        yield stream

@contextmanager
def queue_lock():
    with lock_file('lock') as stream:
        os.fchown(stream.fileno(), 10001, 10001)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def enable_stars():
    from staging_preflight import read_env
    cfg = read_env(ROOT / '.env')
    if not re.fullmatch(r'[1-9][0-9]{0,15}:[A-Za-z0-9_-]{10,256}', cfg.get('TELEGRAM_BOT_TOKEN', '')):
        raise RuntimeError('Telegram token is not configured')
    # Сохраняем выбранный владельцем режим Telegram (тестовый/основной).
    with queue_lock():
        data = read_control('payments.json')
        data.update(stars=True, changed_at=datetime.now(timezone.utc).isoformat(), actor='bootstrap')
        write_control('payments.json', data)


def read_control(name):
    try:
        fd = os.open(CONTROL / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {}
            raw = stream.read(16385)
            if len(raw) > 16384:
                return {}
            data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}



def main():
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    with lock_file('host-lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if '--finalize' in sys.argv:
            with queue_lock():
                try:
                    previous = read_control('status.json')
                except (OSError, ValueError):
                    previous = {}
                if previous.get('state') == 'running':
                    status('failed', code='worker_interrupted')
            return
        with queue_lock():
            request = CONTROL / 'request.json'
            if '--bootstrap' not in sys.argv:
                if not request.exists():
                    return
                try:
                    data = read_control('request.json')
                    if not isinstance(data, dict) or data.get('action') != 'update':
                        raise ValueError('unknown action')
                except (OSError, ValueError):
                    request.unlink(missing_ok=True)
                    status('failed', code='invalid_request')
                    return
                request.unlink()
            status('running')
        old = None
        try:
            old = run('git', 'rev-parse', 'HEAD', capture=True).strip()
            if run('git', 'status', '--porcelain', '--untracked-files=no', capture=True).strip():
                raise RuntimeError('Local modifications; update refused')
            if '--bootstrap' not in sys.argv:
                run('git', 'fetch', '--no-tags', REMOTE, 'refs/heads/release/stable')
                target = run('git', 'rev-parse', 'FETCH_HEAD', capture=True).strip()
                run('git', 'merge-base', '--is-ancestor', old, target)
            else:
                target = old
            running = run('python3', 'scripts/installer_runtime.py', 'compose', '--profile', 'bot', 'ps', '--services', '--status', 'running', capture=True).splitlines()
            profile = ['--profile', 'bot'] if 'bot' in running else []
            # Full private archive includes DB, uploads, .env and deployment configuration.
            run('python3', 'scripts/cabinet_backup.py', 'create', '--allow-downtime')
            if target != old:
                run('git', 'merge', '--ff-only', target)
            run('python3', 'scripts/installer_runtime.py', 'compose', *profile, 'build')
            migrations = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'database').glob('*.sql') if not p.name.startswith('001_'))
            run('python3', 'scripts/installer_runtime.py', 'migrate', *migrations)
            run('python3', 'scripts/installer_runtime.py', 'compose', *profile, 'up', '-d')
            run('python3', 'scripts/installer_runtime.py', 'wait-api')
            run('python3', 'scripts/repair_proxy.py')
            if '--enable-stars' in sys.argv and '--bootstrap' in sys.argv:
                enable_stars()
            status('succeeded', commit=target)
        except Exception as exc:
            # Never restore DB blindly: this would erase concurrent orders/payments.
            # Additive migrations stay; explicit recovery uses the private backup.
            status('failed', previous_commit=old, code=type(exc).__name__)
            raise

if __name__ == '__main__':
    main()
