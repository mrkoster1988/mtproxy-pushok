"""Проверка сырого Telegram Mini App initData до доступа к БД."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException


def validate_init_data(raw, bot_token, *, now=None):
    def invalid():
        raise HTTPException(401, 'miniapp_invalid_init_data')
    try:
        if not isinstance(raw, str) or not 1 <= len(raw.encode()) <= 16384:
            invalid()
    except UnicodeError:
        invalid()
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True,
                          encoding='utf-8', errors='strict', max_num_fields=32)
    except (ValueError, UnicodeError):
        invalid()
    data = dict(pairs)
    if len(data) != len(pairs) or any('\n' in k or '\r' in k or not k for k in data):
        invalid()
    received = data.pop('hash', '')
    if not re.fullmatch('[a-fA-F0-9]{64}', received):
        invalid()
    check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        invalid()
    try:
        auth_date = int(data['auth_date'])
        current = int(time.time()) if now is None else now
        if not current - 300 <= auth_date <= current + 30:
            invalid()
        user = json.loads(data['user'])
        if not isinstance(user, dict) or type(user.get('id')) is not int or not 0 < user['id'] < 2**52:
            invalid()
        for key in ('first_name', 'last_name', 'username'):
            value = user.get(key)
            if (key == 'first_name' and not isinstance(value, str)) or (value is not None and
                    (not isinstance(value, str) or len(value) > 256)):
                invalid()
        if user.get('is_bot'):
            invalid()
    except (ValueError, KeyError, TypeError, RecursionError):
        invalid()
    # Отдельное пространство replay-ключей от Login Widget; порядок URL не влияет.
    replay = hashlib.sha256(('miniapp:' + expected).encode()).hexdigest()
    return {k: user.get(k) for k in ('id', 'first_name', 'last_name', 'username')} | {
        'auth_date': auth_date, 'hash': replay}
