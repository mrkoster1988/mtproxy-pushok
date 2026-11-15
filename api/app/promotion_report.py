"""Чтение ограниченного локального отчёта; внутренние поля не являются API-ответом."""
import json
import os
import re
import stat
from datetime import datetime, timezone

from . import control


def read_report(now=None):
    now = now or datetime.now(timezone.utc)
    try:
        fd = os.open(control.root() / 'promotion-agent.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None
