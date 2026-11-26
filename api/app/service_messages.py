"""Send background notices from the MTProxy cabinet bot and keep cabinet deep links."""
import hmac
import json
import os
import re
import time
from functools import lru_cache
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from urllib.error import HTTPError
from urllib.request import Request as TelegramRequest

from .channel_topic_suggestions import read_url
from .bot_routes import _verify_bot_secret
from .flow_shop_ticket import bridge_key
from .telegram_environment import bot_api_url, is_test_environment

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


CABINET_BOT = 'mtproxy_pushok_bot'


def telegram_call(token, method, payload):
    request = TelegramRequest(bot_api_url(token, method), data=json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json'})
    try:
        reply = json.loads(read_url(request, 8, 131072))
    except HTTPError as error:
        if method in ('sendMessage','sendWidget','editMessageWidgetData','editMessageText') and error.code in (400,403,404):
            try:
                refusal = json.loads(error.read(8192))
                description = str(refusal.get('description', '')).lower() if isinstance(refusal, dict) else ''
            except (OSError, ValueError):
                description = ''
            if method.startswith('editMessage') and 'message is not modified' in description:
                return True
            if method.startswith('editMessage') and 'message to edit not found' in description:
                raise HTTPException(404,'widget_message_missing') from None
            if method=='sendWidget' and (error.code==404 or 'method not found' in description):
                raise HTTPException(400,'widget_unsupported') from None
            if 'chat not found' in description or "can't initiate conversation" in description:
                raise HTTPException(503, 'cabinet_bot_chat_not_started') from None
            if error.code == 403 and ('blocked by the user' in description or 'user is deactivated' in description):
                raise HTTPException(403, 'recipient_blocked') from None
        raise HTTPException(503, 'service_notice_unavailable') from None
    except (OSError, ValueError):
        raise HTTPException(503, 'service_notice_unavailable') from None
    if not isinstance(reply, dict) or reply.get('ok') is not True or not (isinstance(reply.get('result'),dict) or
            (method in ('editMessageWidgetData','editMessageText') and reply.get('result') is True)):
        raise HTTPException(503, 'service_notice_unavailable')
    return reply['result']


@lru_cache(maxsize=1)
def verify_sender(token):
    identity = telegram_call(token, 'getMe', {})
    if identity.get('is_bot') is not True or str(identity.get('username', '')).lower() != CABINET_BOT:
        raise HTTPException(503, 'cabinet_bot_wrong_identity')


def send(notice, shop=False):
    if is_test_environment():
        raise HTTPException(503, 'service_bot_test_environment')
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not re.fullmatch(r'[0-9]{1,16}:[a-zA-Z0-9_-]{25,250}', token):
        raise HTTPException(503, 'cabinet_bot_not_configured')
    verify_sender(token)
    payload = notice.model_dump(exclude_none=True)
    if shop:
        payload['reply_markup'] = {'inline_keyboard': [[{'text': 'Открыть магазин', 'url': 'https://pushok.shop/shop/'}]]}
    elif notice.reply_markup:
        payload['reply_markup'] = {'inline_keyboard': [[cabinet_link(button) for button in row]
                                     for row in notice.reply_markup['inline_keyboard']]}
    message = telegram_call(token, 'sendMessage', payload)
    if not message.get('message_id') or (message.get('chat') or {}).get('id') != notice.chat_id:
        raise HTTPException(503, 'service_notice_unconfirmed')
    from .recipient_history import record
    record(os.getenv('DATABASE_URL'),message)
    return {'sent': True}


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
