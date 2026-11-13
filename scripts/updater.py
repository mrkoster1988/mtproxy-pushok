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
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / '.control'
REMOTE = 'https://github.com/mrkoster1988/mtproxy-pushok.git'

def run(*args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=1800).stdout

def status(state, **fields):
    fd, name = tempfile.mkstemp(dir=CONTROL)
    with os.fdopen(fd, 'w') as stream:
        os.fchown(stream.fileno(), 10001, 10001)
        json.dump(dict(state=state, at=datetime.now(timezone.utc).isoformat(), **fields), stream)
    Path(name).replace(CONTROL / 'status.json')

@contextmanager
def queue_lock():
    with (CONTROL / 'lock').open('a') as stream:
        os.fchown(stream.fileno(), 10001, 10001)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def enable_stars():
    from staging_preflight import read_env
    cfg = read_env(ROOT / '.env')
    if not re.fullmatch(r'[1-9][0-9]{0,15}:[A-Za-z0-9_-]{10,256}', cfg.get('TELEGRAM_BOT_TOKEN', '')):
        raise RuntimeError('Telegram token is not configured')
    # Сохраняем выбранный владельцем режим Telegram (тестовый/основной).
    sys.path.insert(0, str(ROOT / 'api'))
    from app import control
    os.environ['PUSHOK_CONTROL_DIR'] = str(CONTROL)
    with control.locked():
        data = control.read('payments.json', {})
        data.update(stars=True, changed_at=control.now(), actor='bootstrap')
        control.write('payments.json', data)
        os.chown(CONTROL / 'payments.json', 10001, 10001)


def main():
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    with (CONTROL / 'host-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if '--finalize' in sys.argv:
            with queue_lock():
                try:
                    previous = json.loads((CONTROL / 'status.json').read_text())
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
                    data = json.loads(request.read_text())
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
