"""Explicit device consent for existing MTProxy users; no cabinet credentials leave the cabinet.

Only hashes are persisted. The access token is reproducible from the device secret
for the original five-minute grant window, so a lost response never requires
storing a recoverable credential or issuing a second session.
"""
import hashlib
import hmac
import json
import math
import re
import secrets
import os
from datetime import timedelta
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from .telegram_environment import environment_name
from .user_visibility import hide_username
from .flow_shop_ticket import bridge_key, issue_ticket

PREFIX = '/api/v1/flow'
SCOPES = ['profile:read', 'wallet:read']
DEVICE_TTL = 300
SESSION_TTL = 43200
POLL_INTERVAL = 5
MAX_BODY_BYTES = 4096
CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
PUBLIC_CODE = re.compile(r'[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12}')
DEVICE_CODE = re.compile(r'pfd_[A-Za-z0-9_-]{43}')
FLOW_BEARER = re.compile(r'(?i:Bearer) (pfa_[0-9a-f]{64})')
ACCESS_DOMAIN = b'pushok-flow-access-v1\0'
RATE_LOCK = 803175
CAPACITY_LOCK = 803176
MAX_DEVICE_REQUESTS = 1000
MAX_SESSIONS = 10000
MAX_SESSIONS_PER_USER = 10
# Per principal, global; every bucket uses a fixed one-minute window.
# Anonymous issuance has no reliable individual identity behind the shared proxy.
RATE_LIMITS = {'device': (None, 120), 'token': (120, 1200),
               'consent': (60, 600), 'profile': (120, 1200), 'logout': (30, 300), 'shop': (5, 300)}
RATE_BUCKETS = {'/device': 'device', '/device/token': 'token',
                '/device/review': 'consent', '/device/decision': 'consent',
                '/me': 'profile', '/logout': 'logout', '/shop-ticket': 'shop'}


class FlowError(Exception):
    def __init__(self, status, code, *, interval=None):
        super().__init__(code)
        self.status, self.code, self.interval = status, code, interval


def _error_response(exc):
    body, headers = {'error': exc.code}, {}
    if exc.interval is not None:
        body['interval'] = exc.interval
        headers['Retry-After'] = str(exc.interval)
    if exc.status == 401:
        headers['WWW-Authenticate'] = 'Bearer'
    return JSONResponse(body, status_code=exc.status, headers=headers)


class FlowNoStoreMiddleware:
    """Also cover routing errors/unsupported methods, which never reach an APIRoute."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path'] == PREFIX or scope['path'].startswith(PREFIX + '/')):
            return await self.app(scope, receive, send)

        async def no_store(message):
            if message['type'] == 'http.response.start':
                headers = [(key, value) for key, value in message['headers']
                           if key.lower() not in (b'cache-control', b'referrer-policy')]
                message = {**message, 'headers': headers + [
                    (b'cache-control', b'private, no-store'), (b'referrer-policy', b'no-referrer')]}
            await send(message)
        await self.app(scope, receive, no_store)


def digest(secret):
    return hashlib.sha256(secret.encode('ascii')).hexdigest()


def access_token(device_code, key):
    return 'pfa_' + hmac.new(key, ACCESS_DOMAIN + device_code.encode('ascii'), hashlib.sha256).hexdigest()


def connect(dsn):
    return psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row,
                          options='-c statement_timeout=3000 -c lock_timeout=1500')


def _now(conn):
    return conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']


def _remaining(expires_at, now):
    return max(0, math.ceil((expires_at - now).total_seconds()))


def _cleanup(conn, now):
    # Opportunistic cleanup is shared by all workers and bounded by issuance caps.
    conn.execute('DELETE FROM flow_device_requests WHERE expires_at <= %s', (now,))
    conn.execute('DELETE FROM flow_sessions WHERE expires_at <= %s OR revoked_at IS NOT NULL', (now,))
    conn.execute("DELETE FROM flow_rate_windows WHERE window_started < %s - interval '2 minutes'", (now,))


def _rate_key(kind, identity):
    return digest('pushok-flow-rate-v1\0' + kind + '\0' + str(identity))


def _rate_limit(dsn, bucket, principal_key=None, *, principal_only=False):
    """Global first: rotating unknown secrets cannot grow the limiter without bound.

    Commit a consumed global attempt even if the principal limit rejects it.
    Consent checks its verified account separately, after the global gate and auth.
    Socket peers and forwarded headers are not individual account identities.
    """
    retry = None
    principal_limit, global_limit = RATE_LIMITS[bucket]
    limits = [] if principal_only else [('*', global_limit)]
    if principal_key is not None and principal_limit is not None:
        limits.append((principal_key, principal_limit))
    with connect(dsn) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (RATE_LOCK,))
        # Cleanup may cascade from a revoked session to its grant. Use the
        # exchange lock first, avoiding session->grant versus grant->session races.
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (CAPACITY_LOCK,))
        now = _now(conn)
        _cleanup(conn, now)
        window = now.replace(second=0, microsecond=0)
        for key, limit in limits:
            row = conn.execute('SELECT window_started, requests FROM flow_rate_windows WHERE bucket=%s AND client_key=%s',
                               (bucket, key)).fetchone()
            count = row['requests'] if row and row['window_started'] == window else 0
            if count >= limit:
                retry = _remaining(window + timedelta(minutes=1), now)
                break
            conn.execute('''INSERT INTO flow_rate_windows(bucket,client_key,window_started,requests)
                            VALUES(%s,%s,%s,%s) ON CONFLICT(bucket,client_key) DO UPDATE
                            SET window_started=EXCLUDED.window_started, requests=EXCLUDED.requests''',
                         (bucket, key, window, count + 1))
    if retry is not None:
        raise FlowError(429, 'slow_down', interval=max(1, retry))


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError('invalid_json_constant')


async def _read_json(request):
    if request.query_params:
        raise FlowError(400, 'invalid_request')
    lengths = request.headers.getlist('content-length')
    if len(lengths) > 1:
        raise FlowError(400, 'invalid_request')
    if lengths:
        if not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
            raise FlowError(400, 'invalid_request')
        if int(lengths[0]) > MAX_BODY_BYTES:
            raise FlowError(413, 'request_too_large')
    if request.headers.get('content-encoding', 'identity').lower() != 'identity':
        raise FlowError(415, 'invalid_request')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise FlowError(413, 'request_too_large')
        body.extend(chunk)
    if request.method == 'GET':
        if body:
            raise FlowError(400, 'invalid_request')
        return {}
    if not body and request.url.path == PREFIX + '/logout':
        return {}
    if (len(request.headers.getlist('content-type')) != 1
            or request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json'):
        raise FlowError(415, 'invalid_request')
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_json_pairs,
                           parse_constant=_invalid_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise FlowError(400, 'invalid_request') from None
    if not isinstance(value, dict):
        raise FlowError(400, 'invalid_request')
    return value


def _payload(request, keys):
    data = request.state.flow_payload
    if set(data) != set(keys):
        raise FlowError(400, 'invalid_request')
    return data


def _public_code(request, *, decision=False):
    data = _payload(request, ('user_code', 'decision') if decision else ('user_code',))
    code = data['user_code']
    if not isinstance(code, str) or not PUBLIC_CODE.fullmatch(code):
        raise FlowError(400, 'invalid_request')
    if decision and data['decision'] not in ('approve', 'deny'):
        raise FlowError(400, 'invalid_request')
    return data


def _flow_token(request):
    headers = request.headers.getlist('authorization')
    match = FLOW_BEARER.fullmatch(headers[0]) if len(headers) == 1 else None
    if match is None:
        raise FlowError(401, 'unauthorized')
    return match.group(1)


def _request_rate_principal(request, bucket):
    if bucket == 'token':
        data = request.state.flow_payload
        secret = data.get('device_code')
        if set(data) == {'device_code'} and isinstance(secret, str) and DEVICE_CODE.fullmatch(secret):
            return _rate_key('device-code', secret)
    elif bucket in ('profile', 'logout', 'shop'):
        try:
            return _rate_key('flow-access-token', _flow_token(request))
        except FlowError:
            pass
    else:
        # Issuance is global-only. Consent identity is available only after auth.
        return None
    return _rate_key('malformed', bucket)


def _active_user(conn, user_id):
    row = conn.execute('SELECT id FROM users WHERE id=%s AND blocked_at IS NULL FOR SHARE', (user_id,)).fetchone()
    if row is None:
        raise FlowError(401, 'unauthorized')


def _request_for_user(conn, code, user_id, *, lock=False):
    row = conn.execute('SELECT * FROM flow_device_requests WHERE user_code=%s' + (' FOR UPDATE' if lock else ''),
                       (code,)).fetchone()
    now = _now(conn)
    if row is None:
        raise FlowError(404, 'not_found')
    if row['expires_at'] <= now:
        raise FlowError(410, 'expired_token')
    if row['decided_by'] is not None and row['decided_by'] != user_id:
        raise FlowError(404, 'not_found')
    return row, now


def _display_name(row):
    # Flow has no username scope, including when the linked account is an admin.
    person = hide_username(dict(row))
    name = ' '.join(((person.get('first_name') or '')[:160], (person.get('last_name') or '')[:160]))
    # Legacy Telegram names are not schema-constrained; native text rejects ASCII controls.
    name = ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in name)
    return ' '.join(name.split())[:160] or 'Пользователь'


def router_for(get_dsn, get_signing_key, cabinet_user):
    class FlowRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()

            async def route(request):
                try:
                    request.state.flow_payload = await _read_json(request)
                    bucket = RATE_BUCKETS.get(request.url.path.removeprefix(PREFIX))
                    if bucket:
                        principal = _request_rate_principal(request, bucket)
                        await run_in_threadpool(_rate_limit, get_dsn(), bucket, principal)
                    return await original(request)
                except FlowError as exc:
                    return _error_response(exc)
                except RequestValidationError:
                    return _error_response(FlowError(400, 'invalid_request'))
                except HTTPException as exc:
                    codes = {401: 'unauthorized', 403: 'access_denied', 404: 'not_found',
                             409: 'request_conflict', 410: 'expired_token'}
                    return _error_response(FlowError(exc.status_code, codes.get(exc.status_code, 'service_unavailable')))
                except psycopg.Error:
                    # PostgreSQL diagnostics can contain parameters: never relay or log them here.
                    return _error_response(FlowError(503, 'service_unavailable'))
            return route

    router = APIRouter(prefix=PREFIX, tags=['flow-account'], route_class=FlowRoute)

    def consent_user(user=Depends(cabinet_user)):
        _rate_limit(get_dsn(), 'consent', _rate_key('cabinet-user', user['id']), principal_only=True)
        return user

    @router.get('/capabilities')
    def capabilities():
        return {'protocol_version': 1, 'scopes': SCOPES, 'session_ttl_seconds': SESSION_TTL}

    @router.post('/device', status_code=201)
    def create_device(request: Request):
        _payload(request, ())
        get_signing_key()  # Fail closed before handing out a grant that cannot be exchanged.
        with connect(get_dsn()) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (CAPACITY_LOCK,))
            count = conn.execute('SELECT count(*) AS count FROM flow_device_requests').fetchone()['count']
            if count >= MAX_DEVICE_REQUESTS:
                raise FlowError(429, 'slow_down', interval=60)
            now = _now(conn)
            for _ in range(3):
                secret = 'pfd_' + secrets.token_urlsafe(32)
                code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(12))
                created = conn.execute('''INSERT INTO flow_device_requests(id,device_code_hash,user_code,created_at,expires_at)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                    (uuid4(), digest(secret), code, now, now + timedelta(seconds=DEVICE_TTL))).fetchone()
                if created:
                    return {'device_code': secret, 'user_code': code, 'expires_in': DEVICE_TTL, 'interval': POLL_INTERVAL}
            raise FlowError(503, 'service_unavailable')

    @router.post('/device/review')
    def review(request: Request, user=Depends(consent_user)):
        data = _public_code(request)
        with connect(get_dsn()) as conn:
            _active_user(conn, user['id'])
            row, now = _request_for_user(conn, data['user_code'], user['id'])
            return {'client_name': 'Pushok Flow', 'user_code': row['user_code'],
                    'scopes': SCOPES, 'expires_in': _remaining(row['expires_at'], now), 'status': row['state']}

    @router.post('/device/decision')
    def decision(request: Request, user=Depends(consent_user)):
        data = _public_code(request, decision=True)
        desired = 'approved' if data['decision'] == 'approve' else 'denied'
        with connect(get_dsn()) as conn:
            row, now = _request_for_user(conn, data['user_code'], user['id'], lock=True)
            _active_user(conn, user['id'])
            now = _now(conn)
            if row['expires_at'] <= now:
                raise FlowError(410, 'expired_token')
            if row['state'] != 'pending':
                if row['state'] != desired:
                    raise FlowError(409, 'request_conflict')
            else:
                conn.execute('''UPDATE flow_device_requests SET state=%s,decided_by=%s,decided_at=%s WHERE id=%s''',
                             (desired, user['id'], now, row['id']))
            return {'status': desired}

    @router.post('/device/token')
    def token(request: Request):
        data = _payload(request, ('device_code',))
        secret = data['device_code']
        if not isinstance(secret, str) or not DEVICE_CODE.fullmatch(secret):
            raise FlowError(410, 'expired_token')
        key = get_signing_key()
        with connect(get_dsn()) as conn:
            # One order for all exchanges: capacity -> grant -> user -> session.
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (CAPACITY_LOCK,))
            row = conn.execute('SELECT * FROM flow_device_requests WHERE device_code_hash=%s FOR UPDATE',
                               (digest(secret),)).fetchone()
            now = _now(conn)
            if row is None or row['expires_at'] <= now:
                raise FlowError(410, 'expired_token')
            if row['state'] == 'denied':
                raise FlowError(403, 'access_denied')
            if row['state'] == 'approved':
                try:
                    _active_user(conn, row['decided_by'])
                except FlowError:
                    raise FlowError(403, 'access_denied') from None
            now = _now(conn)
            if row['expires_at'] <= now:
                raise FlowError(410, 'expired_token')
            if row['last_polled_at'] is not None:
                wait = _remaining(row['last_polled_at'] + timedelta(seconds=POLL_INTERVAL), now)
                if wait:
                    raise FlowError(429, 'slow_down', interval=wait)
            conn.execute('UPDATE flow_device_requests SET last_polled_at=%s WHERE id=%s', (now, row['id']))
            if row['state'] == 'pending':
                return {'status': 'pending', 'interval': POLL_INTERVAL,
                        'expires_in': _remaining(row['expires_at'], now)}
            credential = access_token(secret, key)
            if row['session_id'] is None:
                counts = conn.execute('''SELECT count(*) AS total,
                    count(*) FILTER (WHERE user_id=%s) AS own FROM flow_sessions''', (row['decided_by'],)).fetchone()
                if counts['total'] >= MAX_SESSIONS or counts['own'] >= MAX_SESSIONS_PER_USER:
                    raise FlowError(429, 'slow_down', interval=60)
                session = conn.execute('''INSERT INTO flow_sessions(id,user_id,token_hash,created_at,expires_at)
                    VALUES(%s,%s,%s,%s,%s) RETURNING *''',
                    (uuid4(), row['decided_by'], digest(credential), now, now + timedelta(seconds=SESSION_TTL))).fetchone()
                conn.execute('UPDATE flow_device_requests SET session_id=%s WHERE id=%s', (session['id'], row['id']))
            else:
                session = conn.execute('SELECT * FROM flow_sessions WHERE id=%s FOR SHARE', (row['session_id'],)).fetchone()
                now = _now(conn)
                if (session is None or session['revoked_at'] is not None or session['expires_at'] <= now
                        or row['expires_at'] <= now
                        or session['user_id'] != row['decided_by']
                        or not hmac.compare_digest(session['token_hash'], digest(credential))):
                    raise FlowError(410, 'expired_token')
            return {'status': 'authorized', 'access_token': credential, 'token_type': 'Bearer',
                    'expires_in': _remaining(session['expires_at'], now), 'scope': SCOPES}

    @router.get('/me')
    def profile(request: Request):
        credential = _flow_token(request)
        with connect(get_dsn()) as conn:
            user = conn.execute('''SELECT u.id,u.first_name,u.last_name,u.username,u.role,
                EXISTS(SELECT 1 FROM verified_profiles v WHERE v.user_id=u.id
                    AND v.phone_verified_at IS NOT NULL AND v.telegram_environment=%s) AS phone_verified
                FROM flow_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
                    AND u.blocked_at IS NULL FOR SHARE OF u,s''',
                (environment_name(), digest(credential))).fetchone()
            if user is None:
                raise FlowError(401, 'unauthorized')
            # wallet_ledger.accounts creates missing rows; this scope is SELECT-only.
            balances = {'RUB': 0, 'BONUS': 0, 'XTR': 0}
            for row in conn.execute('SELECT currency,balance FROM wallet_accounts WHERE user_id=%s', (user['id'],)):
                balances[row['currency']] = row['balance']
            return {'id': str(user['id']), 'display_name': _display_name(user), 'role': user['role'],
                    'phone_verified': user['phone_verified'], 'wallet': balances}

    @router.post('/shop-ticket')
    def shop_ticket(request: Request):
        data = _payload(request, ('consent',))
        if data['consent'] is not True:
            raise FlowError(403, 'access_denied')
        try:
            key = bridge_key(os.getenv('FLOW_SHOP_BRIDGE_SECRET', ''))
        except ValueError:
            raise FlowError(503, 'service_unavailable') from None
        credential = _flow_token(request)
        with connect(get_dsn()) as conn:
            row = conn.execute("""SELECT u.id,u.telegram_user_id FROM flow_sessions s
                JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL
                    AND s.expires_at>clock_timestamp()
                    AND u.blocked_at IS NULL FOR SHARE OF s,u""",
                (digest(credential),)).fetchone()
            if row is None:
                raise FlowError(401, 'unauthorized')
            stamp = int(_now(conn).timestamp())
            ticket = issue_ticket(row['id'], row['telegram_user_id'], key, now=stamp)
        return {'ticket': ticket, 'expires_in': 60}

    @router.post('/logout', status_code=204)
    def logout(request: Request):
        _payload(request, ())
        credential = _flow_token(request)
        with connect(get_dsn()) as conn:
            revoked = conn.execute('''UPDATE flow_sessions s SET revoked_at=clock_timestamp() FROM users u
                WHERE s.user_id=u.id AND s.token_hash=%s AND s.revoked_at IS NULL
                    AND s.expires_at>clock_timestamp() AND u.blocked_at IS NULL RETURNING s.id''',
                (digest(credential),)).fetchone()
            if revoked is None:
                raise FlowError(401, 'unauthorized')
        return Response(status_code=204)

    return router
