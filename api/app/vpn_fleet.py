"""Role-scoped Mini App access to the authoritative Shop WireGuard fleet.

The browser never receives bridge credentials or stored panel tokens. Fleet writes
are restricted to the same owner/developer flags used for infrastructure secrets.
Shop owns node validation, DNS, provisioning, idempotency and migration execution.
"""
import hmac
import ipaddress
import json
import os
import re
import time
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import requests
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .flow_shop_ticket import bridge_key
from .vpn_cabinet import is_staff

DOMAIN = b'pushok-wg-fleet-management-v1:'
BRIDGE_URL = 'https://pushok.shop/shop/api/admin/wg-fleet/bridge'
Action = Literal['create', 'update', 'check', 'state', 'drain', 'retry', 'policy']


def developer(user):
    return bool(user.get('is_owner') or user.get('is_developer'))


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class Endpoint(Input):
    endpoint_ip: str | None = None

    @field_validator('endpoint_ip')
    @classmethod
    def public_ip(cls, value):
        if not value:
            return None
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast:
            raise ValueError('A public WireGuard address is required')
        return str(address)


class Settings(Input):
    api: Literal['wgdashboard'] = 'wgdashboard'
    url: str = Field(min_length=8, max_length=500)
    token: SecretStr = Field(min_length=32, max_length=2048)
    configuration: str = Field(min_length=1, max_length=15, pattern=r'^[a-zA-Z0-9_=+.-]+$')
    schedule_expiry: Literal[True] = True
    panel_timezone: str = Field(default='Europe/Moscow', min_length=1, max_length=80)

    @field_validator('url')
    @classmethod
    def panel_url(cls, value):
        parsed = urlsplit(value)
        _ = parsed.port
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Panel URL must not contain credentials, query or fragment')
        return value.rstrip('/')


class SettingsPatch(Settings):
    token: SecretStr | None = Field(default=None, min_length=32, max_length=2048)


class Create(Endpoint):
    name: str = Field(min_length=1, max_length=80)
    location: str = Field(default='', max_length=80)
    protocol: Literal['wg'] = 'wg'
    capacity: int = Field(ge=0, le=1000000)
    settings: Settings


class NodeId(Input):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class Update(NodeId, Endpoint):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=80)
    capacity: int | None = Field(default=None, ge=0, le=1000000)
    settings: SettingsPatch | None = None


class State(NodeId):
    version: int = Field(ge=1)
    state: Literal['active', 'draining', 'maintenance', 'disabled']


class Drain(NodeId):
    version: int = Field(ge=1)
    deadline_hours: int = Field(ge=1, le=720)
    lost: bool = False


class Policy(Input):
    version: int = Field(ge=0)
    enabled: bool
    standby_count: Literal[1] = 1


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    payload: dict


SCHEMAS = {'create': Create, 'update': Update, 'check': NodeId, 'state': State,
           'drain': Drain, 'retry': NodeId, 'policy': Policy}
ERRORS = {
    'wg_fleet_disabled', 'wg_fleet_management_disabled', 'wg_fleet_invalid_input',
    'wg_fleet_changed', 'wg_fleet_version_conflict', 'wg_fleet_node_missing',
    'wg_fleet_operation_missing', 'wg_fleet_node_unavailable', 'wg_fleet_not_ready',
    'wg_fleet_no_capacity', 'wg_fleet_no_reserve', 'wg_fleet_pending_operation',
    'wg_fleet_integration', 'wg_fleet_change_unconfirmed', 'wg_fleet_dns_unavailable',
    'wg_fleet_dns_not_configured', 'wg_fleet_in_use', 'wg_fleet_request_conflict',
    'wg_fleet_validation_failed', 'wg_fleet_traffic_unverified', 'wg_fleet_expiry_required',
    'wg_fleet_unknown_error', 'wg_fleet_role_required', 'wg_fleet_tls_required',
    'wg_fleet_remote_revoke_unconfirmed', 'wg_fleet_peer_preparation_failed',
    'wg_fleet_archived', 'wg_fleet_dns_not_ready', 'wg_fleet_policy_disabled',
}
ERROR_ALIASES = {'NoHealthyCapacity': 'wg_fleet_no_capacity',
                 'RemoteRevokeUnconfirmed': 'wg_fleet_remote_revoke_unconfirmed',
                 'PeerPreparationFailed': 'wg_fleet_peer_preparation_failed',
                 'AssignmentChanged': 'wg_fleet_changed',
                 'Сервер не найден': 'wg_fleet_node_missing',
                 'Операция не найдена': 'wg_fleet_operation_missing',
                 'Сервер уже изменён. Обновите список.': 'wg_fleet_changed',
                 'Версия изменилась. Обновите список.': 'wg_fleet_changed',
                 'Настройки сервера изменились во время проверки.': 'wg_fleet_changed',
                 'Управление серверами из Mini App не настроено.': 'wg_fleet_management_disabled',
                 'Управление узлами доступно разработчику.': 'wg_fleet_role_required',
                 'Сервер архивирован. Для новой машины добавьте новый узел.': 'wg_fleet_archived',
                 'Сначала подтвердите DNS WireGuard.': 'wg_fleet_dns_not_ready',
                 'Нужны успешная проверка API и свежая проверка трафика WireGuard.': 'wg_fleet_not_ready',
                 'Сначала завершите перенос и отзыв прежних подключений.': 'wg_fleet_pending_operation',
                 'Для узла нужен WGDashboard с заданием окончания доступа и часовым поясом.': 'wg_fleet_expiry_required',
                 'У узла есть подключения. Для новой машины добавьте отдельный сервер.': 'wg_fleet_in_use',
                 'Изменение IP занятого узла выполняется через добавление и перенос.': 'wg_fleet_in_use',
                 'У узла есть подключения. Используйте перенос.': 'wg_fleet_in_use',
                 'Сначала включите управление пулом WireGuard.': 'wg_fleet_policy_disabled',
                 'Этот request_id уже использован для другого действия.': 'wg_fleet_request_conflict',
                 'Этот request_id уже использован.': 'wg_fleet_request_conflict',
                 'Проверьте поля узла и профиль подключения.': 'wg_fleet_invalid_input'}


def error_code(value, default='wg_fleet_unknown_error'):
    # Never forward a provider response, exception message, token or URL as an error.
    if isinstance(value, str):
        value = ERROR_ALIASES.get(value, value)
    return value if isinstance(value, str) and value in ERRORS else default


def text(value, max_length=150):
    return value[:max_length] if isinstance(value, str) else None


def number(value):
    return value if type(value) in (int, float) and value == value and abs(value) != float('inf') else None


def safe_settings(value):
    if not isinstance(value, dict):
        return None
    raw = value.get('url')
    url = None
    if isinstance(raw, str):
        try:
            parsed = urlsplit(raw)
            if parsed.scheme in ('https', 'http') and parsed.hostname:
                # Existing stored URLs are projected without userinfo or query secrets.
                host = parsed.hostname
                if ':' in host:
                    host = '[' + host + ']'
                if parsed.port:
                    host += ':' + str(parsed.port)
                url = urlunsplit((parsed.scheme, host, parsed.path, '', ''))
        except ValueError:
            pass
    return {'api': 'wgdashboard', 'url': url,
            'configuration': text(value.get('configuration'), 15),
            'panel_timezone': text(value.get('panel_timezone'), 80),
            'schedule_expiry': value.get('schedule_expiry') is True,
            'token_configured': value.get('token_configured') is True}


def node(value, include_settings=False):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str):
        raise ValueError('node')
    result = {key: text(value.get(key)) for key in ('id', 'name', 'location', 'domain', 'endpoint_ip', 'dns_state', 'state', 'health', 'probe_status')}
    result.update({key: number(value.get(key)) for key in (
        'load_score', 'capacity', 'occupied', 'primary_count', 'standby_count', 'version', 'checked_at', 'api_checked_at')})
    result['traffic_verified'] = value.get('traffic_verified') is True
    result['archived'] = value.get('archived') is True
    result['error_code'] = error_code(value.get('error_code')) if value.get('error_code') else None
    if include_settings:
        result['settings'] = safe_settings(value.get('settings'))
    return result


def operation(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str):
        raise ValueError('operation')
    result = {key: text(value.get(key)) for key in ('id', 'source_server_id', 'state')}
    result.update({key: number(value.get(key)) for key in ('total', 'completed', 'failed', 'deadline', 'pending_revocations')})
    result['lost'] = value.get('lost') is True
    result['error'] = error_code(value.get('error')) if value.get('error') else None
    return result


def policy(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool or type(value.get('version')) is not int:
        raise ValueError('policy')
    return {'enabled': value['enabled'], 'standby_count': 1, 'version': value['version']}


def project(action, value, include_settings):
    if not isinstance(value, dict):
        raise ValueError('response')
    if action == 'snapshot':
        if not isinstance(value.get('nodes'), list) or not isinstance(value.get('operations'), list):
            raise ValueError('snapshot')
        return {'nodes': [node(row, include_settings) for row in value['nodes']],
                'operations': [operation(row) for row in value['operations']],
                'policy': policy(value.get('policy')), 'can_manage': include_settings}
    if action in ('create', 'update', 'check', 'state'):
        return {'node': node(value.get('node'), include_settings)}
    if action in ('drain', 'migration', 'retry'):
        return {'operation': operation(value.get('operation'))}
    if action == 'policy':
        return {'policy': policy(value.get('policy'))}
    raise ValueError('action')


def relay(action, user, payload=None, request_id=None):
    data = {'action': action, 'actor': str(user['id']),
            'actor_role': 'developer' if developer(user) else 'admin', 'payload': payload or {}}
    if request_id is not None:
        data['request_id'] = str(request_id)
    body = json.dumps(data, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode()
    stamp = str(int(time.time()))
    try:
        key = bridge_key(os.environ.get('FLOW_SHOP_BRIDGE_SECRET', ''))
        signature = hmac.digest(key, DOMAIN + stamp.encode() + b':' + body, 'sha256').hex()
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(BRIDGE_URL, data=body,
                headers={'Content-Type': 'application/json', 'X-Pushok-Management': stamp + ':' + signature},
                timeout=(3, 35), allow_redirects=False)
        if response.status_code not in (200, 201, 202):
            if response.status_code in (400, 401, 403, 404, 409, 422, 502, 503):
                try:
                    problem = response.json()
                    detail = problem.get('detail') if isinstance(problem, dict) else None
                    detail = detail.get('code') if isinstance(detail, dict) else detail
                except ValueError:
                    detail = None
                fallback = 'wg_fleet_integration' if response.status_code in (401, 403, 404) else (
                    'wg_fleet_changed' if response.status_code == 409 else
                    'wg_fleet_invalid_input' if response.status_code in (400, 422) else 'wg_fleet_change_unconfirmed')
                raise HTTPException(response.status_code, error_code(detail, fallback))
            raise ValueError('status')
        result = project(action, response.json(), developer(user))
        return JSONResponse(result, status_code=response.status_code,
                            headers={'Cache-Control': 'private, no-store'})
    except (ValueError, requests.RequestException):
        raise HTTPException(503, 'wg_fleet_change_unconfirmed' if request_id else 'wg_fleet_node_unavailable') from None


def payload_for(action, data):
    try:
        model = SCHEMAS[action].model_validate(data)
    except ValidationError:
        # Pydantic input errors may contain submitted tokens; return only a safe code.
        raise HTTPException(422, 'wg_fleet_invalid_input') from None
    payload = model.model_dump(exclude_none=True)
    if action in ('create', 'update') and model.settings is not None:
        if model.settings.token is not None:
            payload['settings']['token'] = model.settings.token.get_secret_value()
        else:
            payload['settings'].pop('token', None)
    return payload


def router_for(current_user):
    router = APIRouter(prefix='/api/v1/vpn/fleet')

    @router.get('')
    def snapshot(user=Depends(current_user)):
        if not is_staff(user):
            raise HTTPException(403, 'admin_required')
        return relay('snapshot', user)

    @router.get('/migrations/{operation_id}')
    def migration(operation_id: str, user=Depends(current_user)):
        if not is_staff(user):
            raise HTTPException(403, 'admin_required')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', operation_id):
            raise HTTPException(422, 'wg_fleet_invalid_input')
        return relay('migration', user, {'id': operation_id})

    @router.post('/{action}')
    def change(action: Action, data: Any = Body(default=None), user=Depends(current_user)):
        if not developer(user):
            raise HTTPException(403, 'owner_role_required')
        try:
            command = Command.model_validate(data)
        except ValidationError:
            # Envelope diagnostics can also include a submitted panel token.
            raise HTTPException(422, 'wg_fleet_invalid_input') from None
        return relay(action, user, payload_for(action, command.payload), command.request_id)

    return router
