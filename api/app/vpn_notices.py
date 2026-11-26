"""Relay the cabinet bot's authenticated delivery requests to Shop."""
import hmac
import json
import os
import time
from typing import Literal

import requests
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .bot_routes import _verify_bot_secret
from .flow_shop_ticket import bridge_key

router = APIRouter(prefix='/api/v1/internal/bot/vpn-notices', include_in_schema=False)


class Receipt(BaseModel):
    model_config = {'extra':'forbid'}
    id: int = Field(gt=0,strict=True)
    lease: str = Field(pattern=r'^[0-9a-f]{32}$')
    status: Literal['sent','retry','blocked','waiting_chat']


def relay(operation, payload):
    raw = json.dumps(payload,separators=(',',':')).encode()
    stamp = str(int(time.time()))
    try:
        secret = bridge_key(os.environ.get('FLOW_SHOP_BRIDGE_SECRET',''))
        sig = hmac.digest(secret,b'pushok-vpn-notices-v1:'+stamp.encode()+b':'+operation.encode()+b':'+raw,'sha256')
        with requests.Session() as client:
            client.trust_env = False
            response = client.post('https://pushok.shop/shop/api/flow/cabinet-notices/'+operation,data=raw,
                headers={'Content-Type':'application/json','X-Pushok-Management':stamp+':'+sig.hex()},
                timeout=(1.5,2.5), allow_redirects=False)
            response.raise_for_status()
            data = response.json()
        return JSONResponse(data,headers={'Cache-Control':'no-store'})
    except (ValueError,requests.RequestException):
        raise HTTPException(503,'vpn_notifications_unavailable') from None


@router.post('/claim')
def claim(x_pushok_bot_secret: str | None = Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    return relay('claim',{'scheduled_by_cabinet':True,'notification_sender':'mtproxy_pushok_bot'})


class ChatOpened(BaseModel):
    telegram_user_id: int = Field(gt=0, le=2**52, strict=True)


@router.post('/resume')
def resume(chat: ChatOpened, x_pushok_bot_secret: str | None = Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    return relay('resume-chat', {'telegram_id':chat.telegram_user_id})


@router.post('/finish')
def finish(receipt: Receipt, x_pushok_bot_secret: str | None = Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    return relay('finish',receipt.model_dump())
