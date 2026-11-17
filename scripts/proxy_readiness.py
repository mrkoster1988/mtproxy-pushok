"""Локальная проверка автосоздания: без регистрации, запуска служб и вызовов ИИ."""
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import stat
import subprocess
import tempfile


def native_ready(base, binary):
    try:
        for path in (binary, base/'proxy-secret', base/'proxy-multi.conf'):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size == 0:
                return False
        return os.access(binary, os.X_OK)
    except OSError:
        return False


def timers_ready():
    try:
        for name in ('mtproxy-pushok-provisioner.timer', 'mtproxy-pushok-promotion.timer'):
            result = subprocess.run(['systemctl', 'show', name,
                '--property=LoadState,ActiveState,UnitFileState'], capture_output=True, text=True, timeout=5)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            if result.returncode or any(values.get(k) != v for k, v in
                    (('LoadState', 'loaded'), ('ActiveState', 'active'), ('UnitFileState', 'enabled'))):
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def inspect(host, *, source='scheduled'):
    from automatic_binding import connection_status
    checks = {}
    report = dict(schema=1, started_at=datetime.now(timezone.utc).isoformat(),
                  source=source if source in ('manual', 'scheduled') else 'scheduled', checks=checks)

    def finish():
        report['checked_at'] = datetime.now(timezone.utc).isoformat()
        return report

    def probe(name, action, failure):
        try:
            checks[name] = 'ready' if action() else failure
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            checks[name] = failure

    address = None
    alias = None
    try:
        address = host.primary_host()
        alias = host.primary_alias()
        checks['host'] = 'ready'
    except (OSError, ValueError, KeyError):
        address = None
        checks['host'] = 'host_unavailable'
    probe('native', lambda: native_ready(host.BASE, host.BINARY), 'native_unavailable')
    probe('timers', timers_ready, 'timer_unavailable')
    probe('resources', host.resource_ready, 'resources_low')
    checks['telegram'] = 'owner_unavailable'
    try:
        owner = connection_status()
        if owner.get('state') == 'connection_busy':
            checks['telegram'] = 'connection_busy'
        elif (owner.get('enabled') and owner.get('helper_available') and owner.get('session_saved')
                and owner.get('state') in ('connected', 'session_saved', 'unavailable')):
            checks['telegram'] = 'ready'
            seconds = owner.get('retry_seconds', 0)
            if type(seconds) is int and 0 < seconds <= 2147483647:
                checks['telegram'] = 'telegram_flood_wait'
                report['retry_at'] = (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    checks.update(api='api_unavailable', policy='unknown', ports='unknown', capacity='unknown')
    if address:
        try:
            # Этот action выполняется в read-only транзакции и не вызывает модель.
            cfg = host.exchange(dict(action='inspect', host=address, primary_host=alias))
        except BlockingIOError:
            checks['api'] = 'agent_busy'
            return finish()
        except subprocess.TimeoutExpired:
            checks['api'] = 'api_timeout'
            return finish()
        except (ValueError, TypeError):
            checks['api'] = 'api_invalid_response'
            return finish()
        except (OSError, KeyError, RuntimeError, subprocess.SubprocessError):
            return finish()
        try:
            if (not isinstance(cfg, dict)
                    or type(cfg.get('first_port')) is not int or not 1024 <= cfg['first_port'] <= 65533
                    or type(cfg.get('max_instances')) is not int or not 1 <= cfg['max_instances'] <= 3
                    or cfg.get('policy') not in ('ready', 'disabled', 'ai_unavailable', 'owner_unavailable')
                    or cfg.get('capacity') not in ('ready', 'instance_limit', 'preparation_pending', 'preparation_failed', 'daily_limit')
                    or not isinstance(cfg.get('reserved_ports'), list)
                    or any(type(p) is not int or not 1 <= p <= 65535 for p in cfg['reserved_ports'])):
                raise ValueError('invalid_readiness')
        except (ValueError, KeyError, TypeError):
            checks['api'] = 'api_invalid_response'
            return finish()
        # Ответ кабинета уже проверен. Ошибка локального порта не отменяет его.
        try:
            checks.update(api='ready', policy=cfg['policy'], capacity=cfg['capacity'])
            if isinstance(cfg.get('queue'), dict):
                report['queue'] = {key: cfg['queue'][key] for key in
                    ('node_count','campaign_count','delayed_count','max_delay_seconds','wait_minutes','limited')
                    if key in cfg['queue']}
            revision = cfg.get('policy_revision')
            if isinstance(revision, str):
                try:
                    report['policy_revision'] = host.identity(revision)
                except ValueError:
                    pass
            if cfg['capacity'] == 'preparation_pending':
                checks['ports'] = 'preparation_pending'
            elif cfg['capacity'] == 'instance_limit':
                checks['ports'] = 'instance_limit'
            else:
                candidates = set(range(cfg['first_port'], cfg['first_port']+cfg['max_instances']))
                candidates -= set(cfg['reserved_ports']) | {20000, 20001, 20002}
                checks['ports'] = 'ready' if any(host.free_port(p) for p in sorted(candidates)) else 'port_busy'
                if checks['ports'] == 'ready' and not any(host.free_port(p) for p in range(20000, 20003)):
                    checks['ports'] = 'port_busy'
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            checks['ports'] = 'port_check_failed'
    return finish()


def started(report):
    """Начало проверки, а не время завершения медленного docker exec."""
    completed = datetime.fromisoformat(report['checked_at'])
    value = datetime.fromisoformat(report.get('started_at', report['checked_at']))
    if not completed.tzinfo or not value.tzinfo or value > completed or completed > datetime.now(timezone.utc):
        raise ValueError('invalid_report_time')
    return value


def publish(host, report):
    directory = host.ROOT/'.control'
    path = directory/'proxy-readiness.json'
    timestamp = started(report)
    fd = os.open(directory/'proxy-readiness.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('invalid_report_lock')
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous_fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(previous_fd) as previous:
                info = os.fstat(previous.fileno())
                if stat.S_ISREG(info.st_mode) and info.st_size <= 16384:
                    if started(json.loads(previous.read(16385))) > timestamp:
                        return False
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        fd, name = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, 'w') as output:
                os.fchmod(output.fileno(), 0o600)
                # API должен иметь доступ сразу после атомарной замены файла.
                os.fchown(output.fileno(), 10001, 10001)
                json.dump(report, output)
                output.flush(); os.fsync(output.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return True
