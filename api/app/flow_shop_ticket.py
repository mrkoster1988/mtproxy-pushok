"""One-minute signed bridge proof: MTProxy Flow -> Shop (no account password).

The Shop validates this value independently, using a distinct server-only key.
It is a one-time *proof of identity*, not a WGDashboard admin credential.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from uuid import UUID

AUDIENCE = 'pushok-shop'
ISSUER = 'mtproxy'
DOMAIN = b'pushok-flow-shop-v1\0'
KEY_PATTERN = re.compile(r'[a-f0-9]{64}')
TELEGRAM_MAX = 2**52


def bridge_key(raw):
    if not isinstance(raw, str) or not KEY_PATTERN.fullmatch(raw):
        raise ValueError('Bridge key is missing or invalid')
    return bytes.fromhex(raw)


def _urlsafe(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def issue_ticket(mtproxy_user_id, telegram_user_id, secret, now=None):
    user = str(UUID(str(mtproxy_user_id)))
    if type(telegram_user_id) is not int or not 0 < telegram_user_id < TELEGRAM_MAX:
        raise ValueError('Invalid linked Telegram identity')
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise ValueError('Invalid bridge signing key')
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or issued < 1:
        raise ValueError('Invalid timestamp')
    payload = {'v':1, 'iss':ISSUER, 'aud':AUDIENCE, 'sub':user,
               'tg':telegram_user_id, 'iat':issued, 'exp':issued+60,
               'jti':_urlsafe(secrets.token_bytes(24))}
    raw = json.dumps(payload, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
    mac = hmac.new(secret, DOMAIN+raw, hashlib.sha256).hexdigest()
    return 'psb1.'+_urlsafe(raw)+'.'+mac
