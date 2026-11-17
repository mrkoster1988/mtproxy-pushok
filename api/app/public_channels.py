"""Публичность канала через getChat; проверка не подтверждает права владельца.

Сеть вызывается до транзакций с блокировками. Только успешные предпросмотры
кэшируются ненадолго; отправка заявки всегда запрашивает актуальные сведения.
"""
import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from .channel_logos import NoRedirect, matches_channel
from .telegram_environment import bot_api_url, environment_name

_cache = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)


def reference(value):
    match = re.fullmatch(r'(?:@|https://(?:t\.me|telegram\.me)/)?([A-Za-z][A-Za-z0-9_]{3,31})/?', value.strip(), re.I)
    if not match:
        raise HTTPException(422, 'invalid_telegram_channel_url')
    return match[1].lower()


def _telegram_chat(username):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise HTTPException(503, 'channel_lookup_unavailable')
    request = Request(bot_api_url(token, 'getChat'),
                      data=json.dumps({'chat_id': '@' + username}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=4) as reply:
            data = reply.read(65537)
        if len(data) > 65536:
            raise ValueError()
        result = json.loads(data)
    except HTTPError as exc:
        # Не возвращать и не журналировать URL/описание: они могут содержать токен.
        if exc.code in (400, 403):
            raise HTTPException(422, 'channel_public_unavailable') from None
        if exc.code == 429:
            raise HTTPException(429, 'channel_lookup_rate_limit') from None
        raise HTTPException(503, 'channel_lookup_unavailable') from None
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, 'channel_lookup_unavailable') from None
    if not isinstance(result, dict):
        raise HTTPException(503, 'channel_lookup_unavailable')
    if result.get('ok') is not True:
        code = result.get('error_code')
        if code in (400, 403):
            raise HTTPException(422, 'channel_public_unavailable')
        if code == 429:
            raise HTTPException(429, 'channel_lookup_rate_limit')
        raise HTTPException(503, 'channel_lookup_unavailable')
    return result.get('result')


def lookup(value, *, fresh=False, expected_id=None):
    username = reference(value)
    key = (hashlib.sha256(os.getenv('TELEGRAM_BOT_TOKEN', '').encode()).digest(), environment_name(), username)
    chat = None
    with _lock:
        cached = _cache.get(key)
        if not fresh and cached and cached[0] > time.monotonic():
            chat = cached[1]
    if chat is None:
        if not _slots.acquire(blocking=False):
            raise HTTPException(503, 'channel_lookup_unavailable')
        try:
            chat = _telegram_chat(username)
            if not isinstance(chat, dict) or chat.get('type') not in ('private', 'group', 'supergroup', 'channel'):
                raise HTTPException(503, 'channel_lookup_unavailable')
            if chat['type'] != 'channel':
                raise HTTPException(422, 'channel_not_broadcast')
            if not matches_channel(chat, username):
                raise HTTPException(422, 'channel_public_unavailable')
            if type(chat.get('id')) is not int or chat['id'] >= 0:
                raise HTTPException(503, 'channel_lookup_unavailable')
            with _lock:
                _cache[key] = (time.monotonic() + 30, chat)
                _cache.move_to_end(key)
                while len(_cache) > 128:
                    _cache.popitem(last=False)
        except HTTPException:
            with _lock:
                _cache.pop(key, None)
            raise
        finally:
            _slots.release()
    if expected_id is not None and chat['id'] != expected_id:
        raise HTTPException(422, 'channel_identity_changed')
    return dict(title=str(chat.get('title') or '@' + username)[:255], username=username,
                telegram_url='https://t.me/' + username, telegram_channel_id=chat['id'])


def owned_snapshot(dsn, user_id, *, channel_id=None, campaign_id=None):
    """Доступ проверяется до Telegram; соединение закрывается до сетевого запроса."""
    try:
        with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
            if campaign_id is not None:
                row = conn.execute('''SELECT ch.id, ch.telegram_url, ch.telegram_channel_id
                    FROM sponsor_channels ch JOIN campaigns c ON c.channel_id=ch.id
                    WHERE c.id=%s AND c.advertiser_user_id=%s AND c.archived_at IS NULL
                      AND ch.archived_at IS NULL''', (campaign_id, user_id)).fetchone()
            else:
                row = conn.execute('''SELECT id,telegram_url,telegram_channel_id FROM sponsor_channels
                    WHERE id=%s AND owner_user_id=%s AND archived_at IS NULL''', (channel_id, user_id)).fetchone()
    except psycopg.Error:
        raise HTTPException(503, 'database_unavailable') from None
    if not row:
        raise HTTPException(404, 'campaign_not_found' if campaign_id is not None else 'channel_or_active_tariff_not_found')
    return row


def verify_snapshot(snapshot):
    return lookup(snapshot['telegram_url'], fresh=True, expected_id=snapshot['telegram_channel_id'])


def unchanged(row, snapshot):
    if not row or any(row[k] != snapshot[k] for k in ('id', 'telegram_url', 'telegram_channel_id')):
        raise HTTPException(409, 'channel_changed_during_check')
