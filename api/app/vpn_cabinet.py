"""Cabinet-scoped projections of Shop observations. No tunnel credentials cross this boundary."""
import hashlib
import hmac
import json
import os
from threading import Lock
import time
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, Field

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


def is_staff(user):
    return bool(user.get('is_owner') or user.get('is_developer') or user['role'] == 'admin')


class Change(BaseModel):
    model_config = {'extra': 'forbid'}
    action: Literal['extend', 'shorten', 'limit', 'retry']
    subscription_id: str = Field(min_length=1, max_length=100)
    request_id: UUID
    before: int = Field(ge=0, strict=True)
    value: int = Field(ge=0, le=365, strict=True)


def change_subscription(actor, subject, change):
    data = {**change.model_dump(mode='json'), 'actor': str(actor), 'subject': str(subject)}
    body = json.dumps(data, separators=(',', ':'), sort_keys=True).encode()
    stamp = str(int(time.time()))
    try:
        key = bridge_key(os.environ.get('FLOW_SHOP_BRIDGE_SECRET', ''))
        sig = hmac.digest(key, b'pushok-cabinet-vpn-management-v1:' + stamp.encode() + b':' + body, 'sha256')
        with requests.Session() as client:
            client.trust_env = False
            response = client.post('https://pushok.shop/shop/api/flow/cabinet-management', data=body,
                headers={'Content-Type':'application/json', 'X-Pushok-Management':stamp + ':' + sig.hex()},
                timeout=(3, 45), allow_redirects=False)
        if response.status_code in (404, 409, 422):
            detail = response.json().get('detail')
            known = ('vpn_subscription_missing', 'vpn_changed', 'vpn_profiles_in_use', 'vpn_limit_range', 'vpn_days_range')
            raise HTTPException(response.status_code, detail if detail in known else 'vpn_change_conflict')
        if response.status_code != 200 or response.json() != {'ok': True}:
            raise ValueError('unconfirmed')
    except (ValueError, requests.RequestException):
        raise HTTPException(503, 'vpn_change_unconfirmed') from None


def router_for(get_dsn, cabinet_user):
    router = APIRouter(prefix='/api/v1/vpn')

    @router.post('/users/{subject}/management')
    def manage(subject: UUID, change: Change, user=Depends(cabinet_user)):
        if not is_staff(user):
            raise HTTPException(403, 'admin_required')
        change_subscription(user['id'], subject, change)
        global _expires
        with _lock:
            _expires = 0
        return {'ok': True}

    @router.get('/dashboard')
    def dashboard(user=Depends(cabinet_user)):
        data = observations()
        accounts = data['accounts']
        own = accounts.get(str(user['id']))
        staff = is_staff(user)
        public_fields = ('state', 'expires_at', 'online', 'last_seen', 'received', 'sent', 'device_limit', 'tunnel_ips', 'endpoint_ip')
        result = {'sampled_at': data['sampled_at'], 'can_manage': staff,
                  'personal': {k: own.get(k) for k in public_fields} if own else None}
        if staff:
            ids = [UUID(key) for key in accounts]
            people = []
            if ids:
                with psycopg.connect(get_dsn(), row_factory=dict_row) as conn:
                    people = conn.execute("""SELECT id,first_name,last_name,is_developer FROM users
                        WHERE id=ANY(%s) AND blocked_at IS NULL""", (ids,)).fetchall()
            members = [{'id': str(p['id']), 'name': ' '.join(filter(None, (p['first_name'], p['last_name']))) or 'Участник',
                        **accounts[str(p['id'])]} for p in people]
            members.sort(key=lambda p: (-(p.get('last_seen') or 0), p['id']))
            result['members'] = members
            developers = {str(p['id']) for p in people if p['is_developer']}
            result['leaders'] = [{'id': p['id'], 'name': p['name']} for p in members
                                if p['id'] not in developers and p.get('last_seen')][:50]
        return JSONResponse(result, headers={'Cache-Control': 'private, no-store'})

    return router
