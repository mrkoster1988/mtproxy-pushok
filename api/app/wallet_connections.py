"""Owner-only configuration for the two hosted wallet providers."""
import re
from typing import Literal
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from . import control, wallet_acquirers as providers

Provider = Literal['platega', 'antilopay']


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(max_length=160)
    project: str = Field(default='', max_length=160)
    secret: SecretStr | None = None
    vat: Literal['', '10', '22'] = ''
    enabled: bool = Field(strict=True)
    test_mode: bool = Field(strict=True)


def public(provider, values):
    cfg = providers.settings(provider, values)
    return dict(id=provider, account=values.get('MERCHANT_ID' if provider == 'platega' else 'SECRET_ID', ''),
                project=values.get('PROJECT_ID', ''), vat=values.get('VAT', ''),
                secret_saved=bool(values.get('SECRET' if provider == 'platega' else 'PRIVATE_KEY_BASE64')),
                configured=cfg['configured'], enabled=cfg['enabled'], test_mode=cfg['test_mode'])


def updated(provider, data, previous):
    values = dict(previous, ENABLED='1' if data.enabled else '0', TEST_MODE='1' if data.test_mode else '0')
    ident = r'[a-zA-Z0-9_.-]{1,160}'
    if not re.fullmatch(ident, data.account) or (provider == 'antilopay' and not re.fullmatch(ident, data.project)):
        raise HTTPException(422, 'wallet_connection_fields')
    secret_field = 'SECRET' if provider == 'platega' else 'PRIVATE_KEY_BASE64'
    if provider == 'platega':
        values['MERCHANT_ID'] = data.account
    else:
        values.update(SECRET_ID=data.account, PROJECT_ID=data.project, VAT=data.vat)
    if data.secret is not None:
        secret = data.secret.get_secret_value().strip()
        if not re.fullmatch(r'[!-~]{1,16384}', secret):
            raise HTTPException(422, 'wallet_connection_secret')
        values[secret_field] = secret
    old = providers.settings(provider, previous)
    new = providers.settings(provider, values)
    if not new['configured'] or (new['shop'] != old['shop'] and data.secret is None):
        raise HTTPException(422, 'wallet_connection_secret')
    return values


def router_for(dsn, require_owner):
    router = APIRouter()

    @router.get('/api/v1/admin/wallet/connections')
    def connections(response: Response, owner=Depends(require_owner)):
        response.headers['Cache-Control'] = 'private, no-store'
        return [public(p, providers.connection_values(p)) for p in providers.FIELDS]

    @router.put('/api/v1/admin/wallet/connections/{provider}')
    def save(provider: Provider, data: Connection, response: Response, owner=Depends(require_owner)):
        response.headers['Cache-Control'] = 'private, no-store'
        with control.locked(), psycopg.connect(dsn(), connect_timeout=3) as conn:
            previous = providers.connection_values(provider)
            values = updated(provider, data, previous)
            before = providers.settings(provider, previous)
            after = providers.settings(provider, values)
            if (before['shop'], before['test_mode']) != (after['shop'], after['test_mode']):
                if conn.execute("""SELECT 1 FROM wallet_topups WHERE provider=%s
                    AND (status IN ('created','pending','review') OR (status='succeeded' AND NOT test_mode))
                    LIMIT 1""", (provider,)).fetchone():
                    raise HTTPException(409, 'wallet_connection_has_payments')
                # Settled sandbox receipts carry no funds. Stop polling the previous sandbox identity.
                conn.execute("""UPDATE wallet_topups SET next_check_at='infinity' WHERE provider=%s
                             AND provider_account=%s AND test_mode AND status='succeeded'""",
                             (provider, before['shop']))
            conn.execute("""INSERT INTO audit_events(actor_user_id,event_type,details_json)
                         VALUES(%s,'wallet_connection_saved',jsonb_build_object('provider',%s::text,
                         'enabled',%s::boolean,'test_mode',%s::boolean))""",
                         (owner['id'], provider, data.enabled, data.test_mode))
            control.write(f'wallet-{provider}-private.json', values)
        return public(provider, values)

    return router
