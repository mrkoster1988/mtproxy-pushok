"""Send background notices from the configured Pushok ИИ bot and keep cabinet deep links."""
import hmac
import os
import re
import time
from functools import lru_cache
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from . import pushok_support as support
from .bot_routes import _verify_bot_secret
from .flow_shop_ticket import bridge_key
from .telegram_environment import is_test_environment

router = APIRouter(include_in_schema=False)
DOMAIN = b'pushok-shop-telegram-notice-v1:'


class Notice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat_id: int = Field(gt=0, le=2**52, strict=True)
    text: str = Field(min_length=1,max_length=4096)
    parse_mode: Literal['HTML'] | None = None
    reply_markup: dict | None = None


def cabinet_link(button):
    route = button.get('callback_data','')
    if 'web_app' in button:
        url = urlsplit(button['web_app']['url'])
        page = url.fragment.removeprefix('/')
        vpn = parse_qs(url.query).get('vpn',[])
        route = ('vpn_renew' if vpn==['renew'] else 'vpn') if vpn else page
    detail = re.fullmatch(r'(archive|campaign|moderation)/([0-9a-f-]{36})',route)
    if detail:
        route = '_'.join(detail.groups())
    elif route not in ('vpn','vpn_renew','campaigns','moderation','profile','sponsors','create','channels'):
        route = 'dashboard'
    username = os.getenv('TELEGRAM_BOT_USERNAME','mtproxy_pushok_bot')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username): raise HTTPException(503,'cabinet_bot_unavailable')
    return {'text':str(button['text'])[:64], 'url':'https://t.me/'+username+'?startapp='+route}


@lru_cache(maxsize=1)
def verified_identity(key, username):
    return support.verify_bot(key, username)['id']


def send(notice,shop=False):
    if is_test_environment():raise HTTPException(503,'service_bot_test_environment')
    cfg=support.credential()
    if not cfg['token']:raise HTTPException(503,'support_bot_not_configured')
    try:
        identity=verified_identity(cfg['token'],cfg['username'])
        with support.connection(os.environ['DATABASE_URL']) as db:
            contact=db.execute('SELECT 1 FROM support_bot_contacts WHERE telegram_id=%s AND (bot_id=%s OR (bot_id=0 AND %s))',
                        (notice.chat_id,identity,cfg.get('legacy_id',identity)==identity)).fetchone()
        if not contact:raise HTTPException(503,'support_bot_chat_not_started')
        payload=notice.model_dump(exclude_none=True)
        if shop:
            payload['reply_markup']={'inline_keyboard':[[{'text':'Открыть магазин','url':'https://pushok.shop/shop/'}]]}
        elif notice.reply_markup:
            payload['reply_markup']={'inline_keyboard':[[cabinet_link(button) for button in row]
                                          for row in notice.reply_markup['inline_keyboard']]}
        support.bot_call(cfg['token'],'sendMessage',payload)
    except support.RecipientUnavailable as error:
        if getattr(error,'telegram_status',None)==403:raise HTTPException(403,'recipient_blocked') from None
        raise HTTPException(503,'service_notice_unavailable') from None
    except (RuntimeError,KeyError,ValueError):raise HTTPException(503,'service_notice_unavailable') from None
    return {'sent':True}


@router.post('/api/v1/internal/bot/service-message')
def from_bot(notice:Notice,x_pushok_bot_secret:str | None=Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    return send(notice)


@router.post('/api/v1/vpn/shop-service-message')
async def from_shop(request:Request):
    raw=await request.body()
    if len(raw)>24000:raise HTTPException(413)
    proof=re.fullmatch(r'(\d{10}):([a-f0-9]{64})',request.headers.get('X-Pushok-Management',''))
    try:key=bridge_key(os.getenv('FLOW_SHOP_BRIDGE_SECRET',''))
    except ValueError:raise HTTPException(503,'shop_bridge_unavailable') from None
    if (not proof or abs(time.time()-int(proof[1]))>45 or not hmac.compare_digest(
            proof[2],hmac.digest(key,DOMAIN+proof[1].encode()+b':'+raw,'sha256').hex())):raise HTTPException(403)
    try:notice=Notice.model_validate_json(raw)
    except ValueError:raise HTTPException(422) from None
    if notice.parse_mode or notice.reply_markup:raise HTTPException(422)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(send,notice,True)
