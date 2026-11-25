"""Create Shop's Stars invoice and route trusted cabinet-bot callbacks to Shop."""
import hmac
import json
import os
import re
import time
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

import requests
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .bot_routes import _verify_bot_secret
from .flow_shop_ticket import bridge_key
from .vpn_settings import accepts_stars as stars_invoices_enabled
from .stars_checkout import credentials, create_invoice_link, StarsLinkError
from .telegram_environment import is_test_environment

router = APIRouter(include_in_schema=False)
DOMAIN = b'pushok-shop-vpn-stars-v1:'


def digest(operation, stamp, body):
    try:
        key = bridge_key(os.environ.get('FLOW_SHOP_BRIDGE_SECRET',''))
    except ValueError:
        raise HTTPException(503,'shop_bridge_unavailable') from None
    return hmac.digest(key,DOMAIN+stamp.encode()+b':'+operation.encode()+b':'+body,'sha256').hex()


class Invoice(BaseModel):
    model_config = {'extra':'forbid'}
    order_id: UUID
    amount: int = Field(ge=1,le=100000,strict=True)
    days: int = Field(ge=1,le=3650,strict=True)


@router.post('/api/v1/shop/vpn-stars/invoice')
async def invoice(data: Invoice, request: Request):
    proof = re.fullmatch(r'([0-9]{10}):([0-9a-f]{64})',request.headers.get('X-Pushok-Management',''))
    if not proof or abs(time.time()-int(proof[1]))>45 or not hmac.compare_digest(
            digest('invoice',proof[1],await request.body()),proof[2]):
        raise HTTPException(403)
    if is_test_environment() or not stars_invoices_enabled():
        raise HTTPException(409,'cabinet_stars_disabled')
    token,_ = credentials()
    prepared = SimpleNamespace(title='Pushok Flow',description=f'VPN-подписка на {data.days} дн.',
        invoice_payload='shop-vpn:'+str(data.order_id),price_label='Подписка VPN',stars_amount=data.amount)
    try:
        return {'url':await run_in_threadpool(create_invoice_link,token,prepared)}
    except StarsLinkError:
        raise HTTPException(502,'invoice_unavailable') from None


class Event(BaseModel):
    model_config = {'extra':'forbid'}
    invoice_payload: str = Field(pattern=r'^shop-vpn:[a-f0-9-]{36}$')
    telegram_user_id: int = Field(gt=0,strict=True)
    total_amount: int = Field(gt=0,strict=True)
    currency: Literal['XTR']
    query_id: str | None = Field(default=None,min_length=1,max_length=256)
    telegram_payment_charge_id: str | None = Field(default=None,min_length=1,max_length=256)
    occurred_at: int | None = Field(default=None,gt=0,strict=True)


@router.post('/api/v1/internal/bot/vpn-stars/{operation}')
def callback(operation: Literal['precheckout','settle','refund'], data: Event,
             x_pushok_bot_secret: str | None = Header(default=None),
             x_pushok_telegram_environment: str | None = Header(default=None)):
    _verify_bot_secret(x_pushok_bot_secret)
    if is_test_environment() or x_pushok_telegram_environment!='production':
        raise HTTPException(403)
    raw = json.dumps(data.model_dump(exclude_none=True),separators=(',',':')).encode()
    stamp = str(int(time.time()))
    signature = digest(operation,stamp,raw)
    try:
        with requests.Session() as client:
            client.trust_env = False
            result = client.post('https://pushok.shop/shop/api/flow/cabinet-stars/'+operation,data=raw,
                headers={'Content-Type':'application/json','X-Pushok-Management':stamp+':'+signature},
                timeout=(1,2),allow_redirects=False)
            result.raise_for_status()
            return result.json()
    except (ValueError,requests.RequestException):
        raise HTTPException(503,'shop_payment_ack_unavailable') from None
