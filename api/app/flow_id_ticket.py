"""Purpose-separated one-minute MTProxy -> Pushok ID identity proof.

Not interchangeable with a Shop trial ticket. Server keys must be different.
"""
import hashlib
import hmac
import json
import secrets
import time
from uuid import UUID

from .flow_shop_ticket import _urlsafe, TELEGRAM_MAX


DOMAIN = b'pushok-mtproxy-id-v1\0'


def issue_id_ticket(mtproxy_user_id, telegram_user_id, secret, now=None):
    user = str(UUID(str(mtproxy_user_id)))
    if type(telegram_user_id) is not int or not 0 < telegram_user_id < TELEGRAM_MAX:
        raise ValueError('Invalid linked Telegram identity')
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('Invalid ID signing key')
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or issued < 1:
        raise ValueError('Invalid timestamp')
    payload = {
        'v': 1, 'iss': 'mtproxy', 'aud': 'pushok-id', 'sub': user,
        'tg': telegram_user_id, 'iat': issued, 'exp': issued + 60,
        'jti': _urlsafe(secrets.token_bytes(24)),
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
    signature = hmac.new(secret, DOMAIN + raw, hashlib.sha256).hexdigest()
    return 'pid1.' + _urlsafe(raw) + '.' + signature
