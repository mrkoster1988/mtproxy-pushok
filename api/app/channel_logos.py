"""Аватары публичных каналов: авторизация, Bot API, ограниченный кэш без токена в URL клиента."""
import hashlib
import io
import json
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from .telegram_environment import bot_api_url, is_test_environment

MAX_BYTES = 1024 * 1024
MAX_CACHE = 128
_cache = OrderedDict()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(4)
_pending = set()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(request, limit):
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=8) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise ValueError('logo_too_large')
    return data


def _call(token, method, payload):
    data = _read(Request(bot_api_url(token, method), data=json.dumps(payload).encode(),
                         headers={'Content-Type': 'application/json'}, method='POST'), 65536)
    reply = json.loads(data)
    if not isinstance(reply, dict) or reply.get('ok') is not True or not isinstance(reply.get('result'), dict):
        raise ValueError('logo_unavailable')
    return reply['result']


def matches_channel(chat, username):
    aliases = chat.get('active_usernames') or []
    return chat.get('type') == 'channel' and username in [str(chat.get('username', '')).lower(),
        *[str(alias).lower() for alias in aliases if isinstance(alias, str)]]


def _download(token, username):
    chat = _call(token, 'getChat', {'chat_id': '@' + username})
    if not matches_channel(chat, username):
        return None
    photo = chat.get('photo')
    if not isinstance(photo, dict) or not isinstance(photo.get('small_file_id'), str):
        return None
    return _photo_file(token, photo['small_file_id'])


def _photo_file(token, file_id):
    file = _call(token, 'getFile', {'file_id': file_id})
    path = file.get('file_path')
    if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_/-]+\.(?:jpg|jpeg|png)', path)
            or path.startswith('/') or '..' in path or file.get('file_size', 0) > MAX_BYTES):
        return None
    url = 'https://api.telegram.org/file/bot' + token + ('/test/' if is_test_environment() else '/') + path
    data = _read(Request(url), MAX_BYTES)
    with Image.open(io.BytesIO(data)) as img:
        if img.format not in ('JPEG', 'PNG') or img.width * img.height > 1_000_000 or getattr(img, 'n_frames', 1) != 1:
            return None
        img.load()
        img.thumbnail((160, 160))
        output = io.BytesIO()
        img.convert('RGB').save(output, format='JPEG', quality=85)
    return output.getvalue()


def logo(telegram_url):
    match = re.fullmatch(r'https://t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?', telegram_url or '')
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not match or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        return None
    username = match[1].lower()
    return _cached(token, 'channel:' + username, lambda: _download(token, username))


def _download_user(token, telegram_id):
    result = _call(token, 'getUserProfilePhotos', {'user_id': telegram_id, 'offset': 0, 'limit': 1})
    photos = result.get('photos')
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], list):
        return None
    sizes = [p for p in photos[0] if isinstance(p, dict) and isinstance(p.get('file_id'), str)
             and isinstance(p.get('width'), int) and isinstance(p.get('height'), int)
             and p['width'] > 0 and p['height'] > 0]
    if not sizes:
        return None
    photo = min(sizes, key=lambda p: abs(max(p['width'], p['height']) - 160))
    return _photo_file(token, photo['file_id'])


def user_avatar(telegram_id):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not isinstance(telegram_id, int) or telegram_id <= 0 or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        return None
    return _cached(token, 'user:' + str(telegram_id), lambda: _download_user(token, telegram_id))


def _cached(token, identity, download):
    key = (hashlib.sha256(token.encode()).digest(), os.getenv('PUSHOK_TELEGRAM_TEST_ENVIRONMENT', '0'), identity)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(key)
            return cached[1]
        if key in _pending or not _slots.acquire(blocking=False):
            return None
        _pending.add(key)
    try:
        try:
            data = download()
        except (OSError, ValueError, TypeError, KeyError, Image.DecompressionBombError, HTTPException):
            # Ошибки urllib содержат URL с токеном: не журналировать и не отдавать их клиенту.
            data = None
        with _lock:
            _cache[key] = (time.monotonic() + (900 if data else 60), data)
            _cache.move_to_end(key)
            while len(_cache) > MAX_CACHE:
                _cache.popitem(last=False)
        return data
    finally:
        with _lock:
            _pending.discard(key)
        _slots.release()


def router_for(dsn, current_user):
    router = APIRouter()

    @router.get('/api/v1/channels/{channel_id}/logo')
    def channel_logo(channel_id: UUID, user=Depends(current_user)):
        try:
            with psycopg.connect(dsn(), connect_timeout=3, row_factory=dict_row) as conn:
                row = conn.execute('SELECT owner_user_id,telegram_url FROM sponsor_channels WHERE id=%s', (channel_id,)).fetchone()
        except psycopg.Error as exc:
            raise HTTPException(503, 'database_unavailable') from exc
        if not row or (user['role'] not in ('admin', 'operator') and row['owner_user_id'] != user['id']):
            raise HTTPException(404, 'channel_not_found')
        data = logo(row['telegram_url'])
        headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
        return Response(data, status_code=200 if data else 204, media_type='image/jpeg' if data else None, headers=headers)

    return router
