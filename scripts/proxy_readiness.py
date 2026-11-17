"""Локальная проверка автосоздания: без регистрации, запуска служб и вызовов ИИ."""
from datetime import datetime, timedelta, timezone
import json
import os
import stat
import subprocess


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


def inspect(host):
    from automatic_binding import connection_status
    checks = {}
    report = dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(), checks=checks)

    def probe(name, action, failure):
        try:
            checks[name] = 'ready' if action() else failure
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            checks[name] = failure

    address = None
    try:
        address = host.primary_host()
        checks['host'] = 'ready'
    except (OSError, ValueError, KeyError):
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
            cfg = host.exchange(dict(action='inspect', host=address))
            if (type(cfg.get('first_port')) is not int or not 1024 <= cfg['first_port'] <= 65533
                    or type(cfg.get('max_instances')) is not int or not 1 <= cfg['max_instances'] <= 3
                    or cfg.get('policy') not in ('ready', 'disabled', 'ai_unavailable', 'owner_unavailable')
                    or cfg.get('capacity') not in ('ready', 'instance_limit', 'preparation_pending', 'preparation_failed', 'daily_limit')
                    or not isinstance(cfg.get('reserved_ports'), list)
                    or any(type(p) is not int or not 1 <= p <= 65535 for p in cfg['reserved_ports'])):
                raise ValueError('invalid_readiness')
            checks.update(api='ready', policy=cfg['policy'], capacity=cfg['capacity'])
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
            checks.update(api='api_unavailable', policy='unknown', ports='unknown', capacity='unknown')
    return report


def publish(host, report):
    path = host.ROOT/'.control'/'proxy-readiness.json'
    host.atomic(path, json.dumps(report))
    os.chown(path, 10001, 10001)
