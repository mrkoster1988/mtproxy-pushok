"""Настройки ИИ: только администратор, закрытый файл, ключ только на запись."""
from collections import deque
import os
import re
import threading
import time
from urllib.error import HTTPError
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import control

FILE = 'channel-ai-private.json'
DEFAULT_MODEL = 'gpt-4.1-mini'
_checks = deque()
_check_lock = threading.Lock()
_check_slot = threading.BoundedSemaphore(1)


def valid(cfg):
    return (isinstance(cfg, dict) and type(cfg.get('enabled')) is bool
            and isinstance(cfg.get('model'), str)
            and bool(re.fullmatch(r'[A-Za-z0-9._-]{1,80}', cfg['model']))
            and isinstance(cfg.get('key'), str)
            and (not cfg['key'] or bool(re.fullmatch(r'[A-Za-z0-9_-]{1,512}', cfg['key'])))
            and (not cfg['enabled'] or bool(cfg['key'])))


def current():
    saved = control.read(FILE)
    if saved is not None or (control.root() / FILE).exists():
        if valid(saved) and isinstance(saved.get('revision'), str):
            return {**saved, 'source':'settings'}
        # Повреждённый/удалённый в настройках ключ не подменяется ключом из .env.
        return dict(enabled=False, model=DEFAULT_MODEL, key='', revision='invalid', source='invalid')
    cfg = dict(enabled=os.getenv('CHANNEL_TOPIC_AI_ENABLED', '0') == '1',
               model=os.getenv('CHANNEL_TOPIC_AI_MODEL', DEFAULT_MODEL).strip(),
               key=os.getenv('CHANNEL_TOPIC_AI_API_KEY', '').strip(),
               revision='environment', source='environment')
    if not valid(cfg):
        cfg.update(enabled=False, model=DEFAULT_MODEL, key='')
    return cfg


def public(cfg=None):
    cfg = current() if cfg is None else cfg
    return {k:cfg.get(k) for k in ('enabled', 'model', 'revision', 'source')} | {
        'key_saved':bool(cfg['key']), 'checked_at':cfg.get('checked_at')}


def active():
    cfg = current()
    return (cfg['key'], cfg['model']) if cfg['enabled'] else None


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    model: str = Field(pattern=r'^[A-Za-z0-9._-]{1,80}$')
    key: SecretStr | None = None
    clear_key: bool = Field(default=False, strict=True)
    revision: str = Field(min_length=1, max_length=80)


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            from fastapi.responses import JSONResponse
            try:
                response = await handler(request)
            except RequestValidationError:
                # FastAPI обычно включает исходный input в 422; здесь это секрет.
                response = JSONResponse({'detail':'channel_ai_invalid'}, status_code=422)
            except HTTPException as exc:
                response = JSONResponse({'detail':exc.detail}, status_code=exc.status_code, headers=exc.headers)
            response.headers['Cache-Control'] = 'no-store'
            return response
        return safe


def candidate(data, old):
    if data.revision != old['revision']:
        raise HTTPException(409, 'channel_ai_changed')
    key = data.key.get_secret_value().strip() if data.key is not None else ''
    if data.clear_key and (key or data.enabled):
        raise HTTPException(422, 'channel_ai_clear_requires_off')
    cfg = dict(enabled=data.enabled, model=data.model,
               key='' if data.clear_key else key or old['key'])
    if not cfg['key'] and cfg['enabled']:
        raise HTTPException(422, 'channel_ai_key_required')
    if not valid(cfg):
        raise HTTPException(422, 'channel_ai_invalid')
    return cfg


def probe(cfg):
    from .channel_topic_suggestions import ai_topic
    if not cfg['key']:
        raise HTTPException(422, 'channel_ai_key_required')
    with _check_lock:
        now = time.monotonic()
        while _checks and now - _checks[0] >= 60:
            _checks.popleft()
        if len(_checks) >= 6:
            raise HTTPException(429, 'channel_ai_check_limited')
        _checks.append(now)
    if not _check_slot.acquire(blocking=False):
        raise HTTPException(429, 'channel_ai_check_limited')
    try:
        # Реальный короткий запрос проверяет ключ, модель и строгую схему.
        # Пользовательские сведения для проверки не требуются.
        result = ai_topic('Уроки Excel', 'Обучение работе с таблицами и формулами',
                          ['Учебный урок: формулы, таблицы и обучение Excel.'], (cfg['key'], cfg['model']))
        if result != 'education':
            raise HTTPException(502, 'channel_ai_response_invalid')
    except HTTPError as exc:
        code = {401:'channel_ai_key_rejected', 403:'channel_ai_access_denied',
                404:'channel_ai_model_unavailable', 400:'channel_ai_model_unsupported',
                429:'channel_ai_provider_limit'}.get(exc.code, 'channel_ai_unavailable')
        raise HTTPException(502, code) from None
    except ValueError as exc:
        code = 'channel_ai_check_limited' if str(exc) == 'ai_budget_exhausted' else 'channel_ai_response_invalid'
        raise HTTPException(429 if code.endswith('limited') else 502, code) from None
    except (OSError, TypeError, KeyError, IndexError):
        raise HTTPException(502, 'channel_ai_unavailable') from None
    finally:
        _check_slot.release()


def router_for(dsn, require_admin):
    import psycopg
    router = APIRouter(route_class=PrivateRoute)

    @router.get('/api/v1/admin/channel-ai')
    def read_configuration(admin=Depends(require_admin)):
        return public()

    @router.post('/api/v1/admin/channel-ai/check')
    def check_configuration(data: Configuration, admin=Depends(require_admin)):
        cfg = candidate(data, current())
        probe(cfg)
        return dict(verified=True, model=cfg['model'], checked_at=control.now())

    @router.put('/api/v1/admin/channel-ai')
    def save_configuration(data: Configuration, admin=Depends(require_admin)):
        old = current()
        cfg = candidate(data, old)
        if cfg['enabled']:
            probe(cfg)
            cfg['checked_at'] = control.now()
        elif (cfg['key'], cfg['model']) == (old['key'], old['model']):
            cfg['checked_at'] = old.get('checked_at')
        # Не держим общий lock во время сетевого запроса. Позднее сохранение
        # не может затереть настройку другого администратора.
        with control.locked():
            if current() != old:
                raise HTTPException(409, 'channel_ai_changed')
            cfg.update(revision=str(uuid4()), source='settings')
            with psycopg.connect(dsn(), connect_timeout=3) as conn:
                conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'channel_ai_settings_saved',jsonb_build_object('enabled',%s::boolean,'key_changed',%s::boolean))",
                             (admin['id'], cfg['enabled'], cfg['key'] != old['key']))
                control.write(FILE, cfg)
        return public(cfg)

    return router
