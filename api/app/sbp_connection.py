"""Приватная конфигурация ЮKassa; ключ никогда не возвращается API."""
import os
import re
from . import control


def current():
    saved = control.read('sbp-private.json')
    if saved is not None or (control.root() / 'sbp-private.json').exists():
        return saved if isinstance(saved, dict) else {}
    return dict(shop=os.getenv('YOOKASSA_SHOP_ID', ''), secret=os.getenv('YOOKASSA_SECRET_KEY', ''),
                host=os.getenv('PUSHOK_PUBLIC_PAYMENT_HOST', '').strip().lower(),
                test_mode=os.getenv('PUSHOK_SBP_TEST_MODE', '1') != '0')


def public(cfg=None):
    cfg = current() if cfg is None else cfg
    return dict(shop=cfg.get('shop', ''), host=cfg.get('host', ''),
                test_mode=cfg.get('test_mode', True), secret_saved=bool(cfg.get('secret')))


def valid(cfg):
    return bool(re.fullmatch(r'[0-9]{1,32}', cfg.get('shop', '')) and
                re.fullmatch(r'[A-Za-z0-9_\-]{8,256}', cfg.get('secret', '')) and
                re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', cfg.get('host', '')) and
                '..' not in cfg['host'] and type(cfg.get('test_mode')) is bool)


def router_for(dsn, require_admin):
    import psycopg
    from fastapi import APIRouter, Depends, HTTPException, Response
    from pydantic import BaseModel, ConfigDict, Field, SecretStr
    from . import sbp_provider as provider
    router = APIRouter()

    class Configuration(BaseModel):
        model_config = ConfigDict(extra='forbid')
        shop: str = Field(pattern=r'^[0-9]{1,32}$')
        secret: SecretStr | None = None
        host: str = Field(min_length=1, max_length=253)
        test_mode: bool = Field(strict=True)

    @router.get('/api/v1/admin/sbp/connection')
    def read_connection(response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'no-store'
        return public()

    @router.put('/api/v1/admin/sbp/connection')
    def save_connection(data: Configuration, response: Response, admin=Depends(require_admin)):
        response.headers['Cache-Control'] = 'no-store'
        # Конфигурация заменяется атомарно. Сохранение не включает приём оплаты.
        with control.locked():
            old = current()
            cfg = dict(shop=data.shop, host=data.host.strip().lower(), test_mode=data.test_mode,
                       secret=data.secret.get_secret_value() if data.secret else old.get('secret', ''))
            if not valid(cfg):
                raise HTTPException(422, 'sbp_connection_invalid')
            with psycopg.connect(dsn(), connect_timeout=3) as conn:
                if (old.get('shop'), old.get('test_mode'), old.get('host')) != (cfg['shop'], cfg['test_mode'], cfg['host']):
                    used = conn.execute("SELECT EXISTS(SELECT 1 FROM payment_attempts WHERE provider='sbp') OR EXISTS(SELECT 1 FROM wallet_topups WHERE provider='sbp')").fetchone()[0]
                    if used:
                        raise HTTPException(409, 'sbp_connection_has_payments')
                try:
                    result = provider._request('GET', 'https://api.yookassa.ru/v3/me', credentials=(cfg['shop'], cfg['secret']))
                except provider.SBPProviderError:
                    raise HTTPException(502, 'sbp_connection_check_failed') from None
                if (str(result.get('account_id')) != cfg['shop'] or result.get('test') is not cfg['test_mode']
                        or 'sbp' not in result.get('payment_methods', []) or result.get('status') != 'enabled'):
                    raise HTTPException(409, 'sbp_connection_shop_not_ready')
                control.write('sbp-private.json', cfg)
                conn.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'sbp_connection_saved',jsonb_build_object('test_mode',%s::boolean))", (admin['id'],cfg['test_mode']))
        return dict(public(cfg), verified=True)

    return router
