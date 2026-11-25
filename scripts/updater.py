#!/usr/bin/env python3
"""Host-only worker: fixed published release ref, private backup, build and health check."""
import json
import re
from contextlib import contextmanager
import time
import tempfile
import os
import subprocess
import sys
import fcntl
import stat
from pathlib import Path
from datetime import datetime, timezone

from update_screen import publish as publish_update_screen

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / '.control'
REMOTE = 'https://github.com/mrkoster1988/mtproxy-pushok.git'

def run(*args, capture=False):
    env = dict(os.environ)
    if args and args[0] == 'git':
        # No hanging systemd prompt if a read token expires or is missing.
        env['GIT_TERMINAL_PROMPT'] = '0'
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=1800, env=env).stdout

def write_control(filename, data):
    fd, name = tempfile.mkstemp(dir=CONTROL)
    with os.fdopen(fd, 'w') as stream:
        os.fchown(stream.fileno(), 10001, 10001)
        json.dump(data, stream)
    Path(name).replace(CONTROL / filename)

def status(state, **fields):
    write_control('status.json', dict(state=state, at=datetime.now(timezone.utc).isoformat(), **fields))
    try:
        publish_update_screen(ROOT, state, fields.get('stage'))
    except OSError:
        print('Could not publish public update status', file=sys.stderr, flush=True)

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



def running_api_version():
    """Use the version *inside the running API image*, not README from Git.

    A previous git pull can update the checkout while the service continues
    running an older Docker image. Updating the source is not deployment.
    """
    from app import releases

    def private_run(command):
        try:
            proc = subprocess.run(
                command, cwd=ROOT, timeout=20, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('running_api_version_unavailable') from exc
        if proc.returncode:
            raise ValueError('running_api_version_unavailable')
        return proc.stdout.strip()

    container = private_run([
        sys.executable, 'scripts/installer_runtime.py', 'compose', 'ps', '-q', 'api'
    ])
    if not re.fullmatch(r'[0-9a-f]{12,64}', container):
        raise ValueError('running_api_container_unavailable')

    # The API Dockerfile installs app/main.py under this fixed WORKDIR.
    # Parse the *installed source file*, without importing app.main, running
    # its startup code, or making HTTP requests using secret-bearing config.
    probe = """from pathlib import Path
import re
source = Path('/srv/mtproxy-pushok/app/main.py').read_text(encoding='utf-8')
versions = re.findall(r'(?m)^\\s*version\\s*=\\s*"([0-9]{1,4}\\.[0-9]{1,4})"\\s*,\\s*$', source)
if len(versions) != 1:
    raise SystemExit(2)
print(versions[0])
"""
    installed = private_run(['docker', 'exec', container, 'python3', '-c', probe])
    releases.version_key(installed)
    return installed


def published_release():
    # The installed release is the running container, not the repository.
    from app import releases
    return releases.check(running_api_version())


def queue_automatic_update():
    # systemd вызывает службу и при закрытом Mini App. Сеть всегда вне блокировки API.
    with queue_lock():
        if read_control('auto-update.json').get('enabled') is not True:
            return
        if (CONTROL / 'request.json').exists() or read_control('status.json').get('state') == 'running':
            return
        check = read_control('auto-check.json')
        now = time.time()
        previous = check.get('checked_epoch')
        if isinstance(previous, (int, float)) and 0 <= now - previous < 3600:
            return
        check.update(checked_epoch=now, checked_at=datetime.now(timezone.utc).isoformat(), state='checking')
        write_control('auto-check.json', check)

    try:
        release = published_release()
    except (OSError, ValueError):
        release = {'state': 'unavailable'}
    with queue_lock():
        check['state'] = release['state']
        sha = release.get('commit')
        if (release['state'] == 'available' and isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha)
                and sha != check.get('queued_commit')
                and read_control('auto-update.json').get('enabled') is True
                and not (CONTROL / 'request.json').exists()):
            write_control('request.json', {
                'action': 'update', 'actor': 'automatic', 'commit': sha, 'at': check['checked_at'],
            })
            check.update(queued_commit=sha, state='queued')
        write_control('auto-check.json', check)


def main(recovery_target=None):
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    with lock_file('host-lock') as lock:
        if '--finalize' in sys.argv:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Другой обновлятор ещё работает: его статус менять нельзя.
                return
        else:
            # Ожидаем владельца, сохраняя запрос. systemd ограничивает общее время.
            # После освобождения блокировки запрос проверяется заново.
            print('Waiting for updater lock', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        if '--finalize' in sys.argv:
            with queue_lock():
                try:
                    previous = read_control('status.json')
                except (OSError, ValueError):
                    previous = {}
                if previous.get('state') == 'running':
                    status('failed', code='worker_interrupted', stage=previous.get('stage'))
            return
        request = CONTROL / 'request.json'
        automatic_target = None
        if not recovery_target and '--bootstrap' not in sys.argv and not request.exists():
            queue_automatic_update()
        with queue_lock():
            if not recovery_target and '--bootstrap' not in sys.argv:
                if not request.exists():
                    previous = read_control('status.json')
                    if previous.get('state') == 'running':
                        status('failed', code='worker_interrupted', stage=previous.get('stage'))
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
                if data.get('actor') == 'automatic':
                    if read_control('auto-update.json').get('enabled') is not True:
                        return
                    automatic_target = data.get('commit')
                    if not isinstance(automatic_target, str) or not re.fullmatch('[0-9a-f]{40}', automatic_target):
                        status('failed', code='invalid_request')
                        return
            status('running')
        old = None
        stage = "preflight"
        try:
            old = run('git', 'rev-parse', 'HEAD', capture=True).strip()
            if run('git', 'status', '--porcelain', '--untracked-files=no', capture=True).strip():
                raise RuntimeError('Local modifications; update refused')
            if '--bootstrap' not in sys.argv:
                run('git', 'fetch', '--no-tags', REMOTE, 'refs/heads/release/stable')
                target = run('git', 'rev-parse', 'FETCH_HEAD', capture=True).strip()
                if recovery_target and target != recovery_target:
                    raise RuntimeError('Stable release changed during recovery. Retry with the current release.')
                if automatic_target and target != automatic_target:
                    status('idle', code='published_release_changed')
                    return
                run('git', 'merge-base', '--is-ancestor', old, target)
            else:
                target = old
            running = run('python3', 'scripts/installer_runtime.py', 'compose', '--profile', 'bot', '--profile', 'flow-push', 'ps', '--services', '--status', 'running', capture=True).splitlines()
            profile = ['--profile', 'bot'] if 'bot' in running else []
            if 'flow-push' in running:
                profile.extend(['--profile', 'flow-push'])
            # Full private archive includes DB, uploads, .env and deployment configuration.
            stage = "backup"
            status('running', stage=stage)
            if recovery_target:
                run('python3', str(Path(__file__).with_name('cabinet_backup.py')), 'create',
                    '--allow-downtime', '--root', str(ROOT))
            else:
                run('python3', 'scripts/cabinet_backup.py', 'create', '--allow-downtime')
            stage = "source"
            status('running', stage=stage)
            if target != old:
                run('git', 'merge', '--ff-only', target)
            run('bash', 'scripts/install_updater.sh', '--install-only')
            stage = "build"
            status('running', stage=stage)
            run('python3', 'scripts/installer_runtime.py', 'compose', *profile, 'build')
            stage = "migration"
            status('running', stage=stage)
            migrations = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'database').glob('*.sql') if not p.name.startswith('001_'))
            run('python3', 'scripts/installer_runtime.py', 'migrate', *migrations)
            stage = "restart"
            status('running', stage=stage)
            run('python3', 'scripts/installer_runtime.py', 'compose', *profile, 'up', '-d')
            stage = "health"
            status('running', stage=stage)
            run('python3', 'scripts/installer_runtime.py', 'wait-api')
            stage = "proxy"
            status('running', stage=stage)
            run('python3', 'scripts/repair_proxy.py')
            if '--enable-stars' in sys.argv and '--bootstrap' in sys.argv:
                enable_stars()
            status('succeeded', commit=target)
        except Exception as exc:
            # Never restore DB blindly: this would erase concurrent orders/payments.
            # Additive migrations stay; explicit recovery uses the private backup.
            status('failed', previous_commit=old, code=type(exc).__name__, stage=stage)
            raise

if __name__ == '__main__':
    recovery_target = None
    if sys.argv[1:2] == ['--recover-flow-push']:
        if len(sys.argv) != 3 or not re.fullmatch('[0-9a-f]{40}', sys.argv[2]):
            raise SystemExit('Expected the stable Git commit')
        ROOT = Path('/opt/mtproxy-pushok')
        CONTROL = ROOT / '.control'
        recovery_target = sys.argv[2]
    sys.path.insert(0, str(ROOT / 'api'))
    os.environ['PUSHOK_CONTROL_DIR'] = str(CONTROL)
    main(recovery_target)
