"""Хостовая часть очереди. Вызывается только внутри блокировок promotion_agent."""
import json
from pathlib import Path
import stat
import subprocess

ENABLED = Path('/var/lib/mtproxy-pushok/telegram-owner/automatic.enabled')
PYTHON = '/opt/mtproxy-pushok-telegram-owner-venv/bin/python'


def enabled():
    try:
        info = ENABLED.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
    except FileNotFoundError:
        return False


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
