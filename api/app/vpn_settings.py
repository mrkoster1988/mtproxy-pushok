"""Cabinet policy, with optimistic edits and role checks at the HTTP boundary."""
import hashlib
import hmac
import json
import os
import re
import time
from string import Formatter
from typing import Annotated, Literal

import psycopg
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .flow_shop_ticket import bridge_key
from .vpn_cabinet import is_staff

Day = Annotated[int, Field(strict=True, ge=1, le=365)]
Repeat = Annotated[int, Field(strict=True, ge=0, le=365)]
Text = Annotated[str, Field(min_length=1,max_length=700)]
DOMAIN = b'pushok-vpn-settings-v1:'


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True,str_strip_whitespace=True)
    stars_enabled: bool | None = None
    expiry_enabled: bool = True
    expiry_days: list[Day] = Field(default_factory=lambda:[7,3,1],min_length=1,max_length=12)
    expiry_text: Text = 'Ваша VPN-подписка действует до {дата}. Осталось дней: {дней}.'
    never_enabled: bool = True
    never_days: Day = 1
    never_repeat: Repeat = 7
    never_text: Text = 'Вы ещё не подключались к VPN. Откройте раздел VPN в кабинете, чтобы начать.'
    idle_enabled: bool = True
    idle_days: Day = 14
    idle_repeat: Repeat = 7
    idle_text: Text = 'Последнее подключение к VPN было {дней} дн. назад. Доступ можно проверить в кабинете.'

    @field_validator('expiry_days')
    @classmethod
    def thresholds(cls,v):
        if len(set(v))!=len(v): raise ValueError('Повторяющийся срок')
        return sorted(v,reverse=True)

    @field_validator('expiry_text','never_text','idle_text')
    @classmethod
    def template(cls,value):
        for _,field,spec,conversion in Formatter().parse(value):
            if field is not None and (field not in ('имя','дней','дата') or spec or conversion):
                raise ValueError('Неизвестная подстановка')
        return value


class Save(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0,strict=True)
    policy: Policy


def read(conn):
    row=conn.execute('SELECT policy,version FROM vpn_settings WHERE id=true').fetchone()
    return {'version':row['version'],'policy':Policy.model_validate(row['policy']).model_dump()}


def accepts_stars():
    from .payment_gate import stars_invoices_enabled
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=2) as conn:
        setting=read(conn)['policy']['stars_enabled']
    # Existing explicit cabinet payment decisions take precedence until VPN has its own override.
    return stars_invoices_enabled() if setting is None else setting


def private(value):
    return JSONResponse(value,headers={'Cache-Control':'private, no-store'})


def admin(user):
    if not is_staff(user): raise HTTPException(403,'admin_required')


def signed(action,stamp,body):
    key=bridge_key(os.getenv('FLOW_SHOP_BRIDGE_SECRET',''))
    return hmac.digest(key,DOMAIN+stamp.encode()+b':'+action.encode()+b':'+body,'sha256').hex()


def shop(action,data):
    body=json.dumps(data,separators=(',',':')).encode()
    stamp=str(int(time.time()))
    try:
        with requests.Session() as c:
            c.trust_env=False
            r=c.post('https://pushok.shop/shop/api/flow/vpn-settings/'+action,data=body,
                headers={'X-Pushok-Management':stamp+':'+signed(action,stamp,body),'Content-Type':'application/json'},
                timeout=(3,10),allow_redirects=False)
            if r.status_code in (409,422): raise HTTPException(r.status_code,'vpn_policy_rejected')
            r.raise_for_status()
            return private(r.json())
    except (ValueError,requests.RequestException): raise HTTPException(503,'vpn_shop_unavailable') from None


class Access(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool
    days:int=Field(ge=1,le=30)
    max_users:int=Field(ge=1,le=100000)
    version:int=Field(ge=1)


def router_for(get_dsn,current_user):
    router=APIRouter(prefix='/api/v1/vpn')
    @router.get('/settings')
    def settings(user=Depends(current_user)):
        admin(user)
        with psycopg.connect(get_dsn(),row_factory=dict_row) as conn:return private(read(conn))

    @router.put('/settings')
    def save(data:Save,user=Depends(current_user)):
        admin(user)
        with psycopg.connect(get_dsn(),row_factory=dict_row) as conn:
            if not conn.execute('UPDATE vpn_settings SET policy=%s::jsonb,version=version+1 WHERE id AND version=%s',
                                (data.policy.model_dump_json(),data.version)).rowcount:
                raise HTTPException(409,'vpn_policy_changed')
            return private(read(conn))

    @router.get('/settings/access')
    def access(user=Depends(current_user)):
        admin(user)
        return shop('read',{})

    @router.put('/settings/access')
    def update_access(data:Access,user=Depends(current_user)):
        admin(user)
        return shop('access',data.model_dump())

    @router.post('/peer-owners',include_in_schema=False)
    async def owners(req:Request):
        body=await req.body()
        proof=re.fullmatch(r'(\d{10}):([0-9a-f]{64})',req.headers.get('X-Pushok-Management',''))
        if not proof or abs(time.time()-int(proof[1]))>45 or not hmac.compare_digest(proof[2],signed('owners',proof[1],body)):
            raise HTTPException(403)
        try: ids=json.loads(body)['ids']
        except (ValueError,KeyError,TypeError): raise HTTPException(422) from None
        if not isinstance(ids,list) or len(ids)>200 or any(type(x)!=int or not 0<x<2**52 for x in ids):
            raise HTTPException(422)
        with psycopg.connect(get_dsn(),row_factory=dict_row) as conn:
            rows=conn.execute("""SELECT telegram_user_id,username,first_name,last_name FROM users
                               WHERE telegram_user_id=ANY(%s) AND blocked_at IS NULL""",(ids,)).fetchall()
        return private({'owners':{str(r['telegram_user_id']):{'name':' '.join(filter(None,(r['first_name'],r['last_name']))),'username':r['username']} for r in rows}})
    return router
