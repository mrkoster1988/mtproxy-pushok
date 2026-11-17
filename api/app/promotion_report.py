"""Чтение ограниченного локального отчёта; внутренние поля не являются API-ответом."""
import json
import os
import re
import stat
from datetime import datetime, timezone

from . import control


def owner_fields(data, now):
    if not isinstance(data, dict) or data.get('state') not in {
            'connected', 'session_saved', 'not_connected', 'revoked', 'unavailable', 'connection_busy', 'invalid_state'}:
        return None
    if any(type(data.get(k)) is not bool for k in ('enabled', 'session_saved', 'helper_available')):
        return None
    result = {k: data[k] for k in ('state', 'enabled', 'session_saved', 'helper_available')}
    name = data.get('username')
    if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_]{1,32}', name):
        result['username'] = name
    try:
        checked = datetime.fromisoformat(data['checked_at'])
        if checked.tzinfo and checked <= now:
            result['checked_at'] = checked.isoformat()
    except (ValueError, TypeError, KeyError):
        pass
    return result


def read_report(now=None, node_id=None):
    now = now or datetime.now(timezone.utc)
    try:
        path = control.root() / 'promotion-agent.json'
        if node_id is not None:
            from uuid import UUID
            candidate = control.root() / ('promotion-agent-'+str(UUID(str(node_id)))+'.json')
            if candidate.exists():
                path = candidate
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                return None
            data = json.loads(source.read(8193))
        if not isinstance(data, dict) or not isinstance(data.get('code'), str):
            return None
        if not re.fullmatch(r'[a-z_]{1,48}', data['code']):
            return None
        checked = datetime.fromisoformat(data['checked_at'])
        if checked.tzinfo is None or checked > now:
            return None
        result = {'code': data['code'], 'checked_at': checked,
                  'recent': (now - checked).total_seconds() < 120}
        for key, pattern in [('host', r'[a-zA-Z0-9.:-]{1,253}'),
                             ('node_id', r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'),
                             ('plan_revision', r'[0-9a-f]{64}')]:
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(pattern, value):
                result[key] = value
        port = data.get('port')
        if type(port) is int and 1 <= port <= 65535:
            result['port'] = port
        owner = owner_fields(data.get('telegram_owner'), now)
        if owner:
            result['telegram_owner'] = owner
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def owner_connection(now=None):
    now = now or datetime.now(timezone.utc)
    report = read_report(now)
    result = {'code': 'unknown', 'title': 'Подключение ещё не проверено', 'tone': 'muted',
              'next_action': 'Подключите аккаунт владельца на VPS. Затем обновите состояние.',
              'enabled': None, 'session_saved': None, 'last_verified_at': None}
    if not report:
        return result
    result['agent_checked_at'] = report['checked_at'].isoformat()
    if not report['recent']:
        return dict(result, code='agent_stale', title='Нет свежего отчёта агента', tone='warning',
                    next_action='Обновите состояние. Если отчёт не появился, проверьте службу агента на VPS.')
    owner = report.get('telegram_owner')
    if not owner:
        return dict(result, next_action='Обновите приложение и агент на VPS для проверки подключения.')
    result.update(enabled=owner['enabled'], session_saved=owner['session_saved'])
    if report.get('host'):
        result['host'] = report['host']
    messages = {
        'not_connected': ('Нужен вход в Telegram', 'Подключите аккаунт, в котором зарегистрирован прокси.', 'warning'),
        'revoked': ('Вход в Telegram отозван', 'На VPS выполните disconnect, затем enable и войдите заново.', 'danger'),
        'unavailable': ('Не удалось проверить Telegram', 'Сессия сохранена. Повторите status на VPS позже; новый код входа не нужен.', 'warning'),
        'invalid_state': ('Не удалось прочитать подключение', 'Проверьте подключение командой status на VPS. Сессию вручную не удаляйте.', 'danger'),
        'connection_busy': ('Подключение проверяется', 'Другой процесс работает с аккаунтом. Обновите состояние через минуту.', 'muted'),
        'disabled': ('Автопривязка выключена', 'Сессия сохранена. Для подготовки новых каналов выполните enable на VPS.', 'muted'),
        'helper_missing': ('Компонент подключения не установлен', 'Повторите команду enable на VPS для установки компонента.', 'warning'),
        'session_saved': ('Сессия сохранена', 'Вход будет проверен перед следующей привязкой. Проверить сейчас можно командой status на VPS.', 'muted'),
        'ready': ('Автопривязка включена', 'Агент готовит одобренные и оплаченные заявки. Регистрация прокси проверяется отдельно.', 'success'),
    }
    code = owner['state']
    if code in ('connected', 'session_saved'):
        if not owner['enabled']:
            code = 'disabled'
        elif not owner['helper_available']:
            code = 'helper_missing'
        else:
            checked = datetime.fromisoformat(owner['checked_at']) if owner.get('checked_at') else None
            code = 'ready' if code == 'connected' and checked and (now-checked).total_seconds() < 900 else 'session_saved'
    title, action, tone = messages[code]
    result.update(code=code, title=title, next_action=action, tone=tone)
    if owner['state'] == 'connected':
        result['last_verified_at'] = owner.get('checked_at')
        if owner.get('username'):
            result['account_username'] = owner['username']
    return result
