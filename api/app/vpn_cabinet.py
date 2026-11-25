"""Cabinet-scoped projections of Shop observations. No tunnel credentials cross this boundary."""
import hashlib
import hmac
import os
from threading import Lock
import time
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .flow_shop_ticket import bridge_key

_lock = Lock()
_expires = 0.0
_sample = None


def observations():
    global _sample, _expires
    with _lock:
        if time.monotonic() < _expires:
            if _sample is None:
                raise HTTPException(503, 'vpn_observations_unavailable')
            return _sample
        try:
            stamp = str(int(time.time()))
            key = bridge_key(os.environ.get('FLOW_SHOP_BRIDGE_SECRET', ''))
            sig = hmac.new(key, ('pushok-vpn-presence-v1:' + stamp).encode(), hashlib.sha256).hexdigest()
            with requests.Session() as client:
                client.trust_env = False
                response = client.get('https://pushok.shop/shop/api/flow/cabinet-presence',
                    headers={'X-Pushok-Presence': stamp + ':' + sig}, timeout=(3, 8), allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError('status')
                data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('accounts'), dict) or type(data.get('sampled_at')) is not int:
                raise ValueError('schema')
        except (ValueError, requests.RequestException):
            _sample, _expires = None, time.monotonic() + 5
            raise HTTPException(503, 'vpn_observations_unavailable') from None
        _sample, _expires = data, time.monotonic() + 20
        return data


def router_for(get_dsn, cabinet_user):
    router = APIRouter(prefix='/api/v1/vpn')

    @router.get('/dashboard')
    def dashboard(user=Depends(cabinet_user)):
        data = observations()
        accounts = data['accounts']
        own = accounts.get(str(user['id']))
        staff = bool(user.get('is_owner') or user.get('is_developer') or user['role'] == 'admin')
        connected = [UUID(key) for key, value in accounts.items() if staff or value.get('last_seen')]
        people = []
        if connected:
            with psycopg.connect(get_dsn(), row_factory=dict_row) as conn:
                people = conn.execute("""SELECT id,first_name,last_name,is_developer FROM users
                    WHERE id=ANY(%s) AND blocked_at IS NULL""", (connected,)).fetchall()
        members = [{'id': str(p['id']), 'name': ' '.join(filter(None, (p['first_name'], p['last_name']))) or 'Участник',
                    'developer': p['is_developer'], 'stats': accounts[str(p['id'])]} for p in people]
        members.sort(key=lambda p: (-(p['stats'].get('last_seen') or 0), p['id']))
        leaders = [{'id': p['id'], 'name': p['name']} for p in members
                  if not p['developer'] and p['stats'].get('last_seen')][:50]
        result = {'sampled_at': data['sampled_at'], 'personal': own, 'leaders': leaders}
        if staff:
            result['members'] = [{'id': p['id'], 'name': p['name'], **p['stats']} for p in members]
        return JSONResponse(result, headers={'Cache-Control': 'private, no-store'})

    return router
