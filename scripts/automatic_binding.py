"""Хостовая часть очереди. Вызывается только внутри блокировок promotion_agent."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import stat
import subprocess
from telegram_owner import PrivateState, OwnerError, DEFAULT_STATE

ENABLED = Path('/var/lib/mtproxy-pushok/telegram-owner/automatic.enabled')
PYTHON = '/opt/mtproxy-pushok-telegram-owner-venv/bin/python'


def enabled():
    try:
        info = ENABLED.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
    except OSError:
        return False


def connection_status():
    """Локальная диагностика: не подключается к Telegram и не выдаёт секреты."""
    result = {'state': 'not_connected', 'enabled': False, 'session_saved': False,
              'helper_available': os.access(PYTHON, os.X_OK)}
    if not DEFAULT_STATE.exists():
        return result
    try:
        with PrivateState(DEFAULT_STATE) as state:
            result['enabled'] = state.automatic_enabled()
            saved = state.read()
            if not saved:
                return result
            result.update(state='session_saved', session_saved=True)
            try:
                health = state.read_file('health.json')
                checked = datetime.fromisoformat(health['checked_at']) if health else None
                if not checked or not checked.tzinfo or checked > datetime.now(timezone.utc):
                    return result
                if health['state'] in ('revoked', 'unavailable'):
                    result['state'] = health['state']
                elif health['state'] == 'connected' and health.get('telegram_user_id') == saved['user_id']:
                    result.update(state='connected', telegram_user_id=saved['user_id'])
                    name = health.get('username')
                    import re
                    if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}', name):
                        result['username'] = name
                else:
                    return result
                result['checked_at'] = checked.isoformat()
            except (OSError, ValueError, KeyError, TypeError, OwnerError):
                pass
    except OwnerError as exc:
        result['state'] = 'connection_busy' if str(exc) == 'connection_busy' else 'invalid_state'
    except (OSError, ValueError, TypeError):
        result['state'] = 'invalid_state'
    return result


def helper(root, request):
    try:
        result = subprocess.run([PYTHON, str(root / 'scripts/telegram_binding_worker.py')],
            input=json.dumps(request), capture_output=True, text=True, timeout=115, check=True)
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or type(data.get('ok')) is not bool:
            raise ValueError('invalid_helper_result')
        return data
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ok': False, 'code': 'owner_helper_unavailable', 'retry_seconds': 300}


def tick(agent, request):
    if not enabled():
        return
    connection = connection_status()
    if (not connection['enabled'] or not connection['helper_available']
            or connection['state'] not in ('connected', 'session_saved', 'unavailable')):
        return
    job = agent.exchange(dict(request, action='owner_claim'))
    if job.get('state') != 'job':
        return
    # Сначала останавливаем показ предыдущего тега. Запись baseline переживает
    # обрыв между изменением Telegram и ответом БД, включая отключение автоматики.
    if agent.STATE.exists():
        saved = json.loads(agent.STATE.read_text())
        saved['baseline'] = ''
        saved['restore_required'] = True
        agent.atomic(agent.STATE, json.dumps(saved))
    agent.apply('')
    agent.STATE.unlink(missing_ok=True)
    result = helper(agent.ROOT, dict(job, action='bind' if job['phase'] == 'bind' else 'inspect'))
    base = dict(request, job_id=job['job_id'])
    if not result['ok']:
        agent.exchange(dict(base, action='owner_error', code=result.get('code'), retry_seconds=result.get('retry_seconds',300)))
        return
    action = 'owner_authorize' if job['phase'] == 'inspect' else 'owner_verified'
    accepted = agent.exchange(dict(base, action=action, **{k:v for k,v in result.items() if k != 'ok'}))
    if not accepted.get('accepted'):
        agent.exchange(dict(base, action='owner_error', code='binding_not_verified'))
