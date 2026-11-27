"""Customer renewal through an authenticated cabinet session."""
import hmac
import json
import os
import time
from uuid import UUID, uuid4

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

from .flow_shop_ticket import bridge_key
from .vpn_settings import accepts_stars
from .stars_checkout import credentials
from .telegram_environment import is_test_environment

DOMAIN = b'pushok-miniapp-vpn-checkout-v1:'


class Choice(BaseModel):
    model_config = {'extra':'forbid'}
    plan: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    plan_version: int = Field(ge=1,strict=True)
    amount: int = Field(gt=0,le=100000,strict=True)
    policy_version: int = Field(ge=0,strict=True)
    accept_terms: Literal[True]
    request_id: UUID


def exchange(action,user,**fields):
    if is_test_environment():
        raise HTTPException(409,'vpn_test_store')
    raw=json.dumps({**fields,'subject':str(user['id']),'telegram_id':user['telegram_user_id'],
                    'nonce':str(uuid4())},separators=(',',':')).encode()
    stamp=str(int(time.time()))
    try:
        key=bridge_key(os.getenv('FLOW_SHOP_BRIDGE_SECRET',''))
        signature=hmac.digest(key,DOMAIN+stamp.encode()+b':'+action.encode()+b':'+raw,'sha256').hex()
        with requests.Session() as client:
            client.trust_env=False
            reply=client.post('https://pushok.shop/shop/api/flow/miniapp-vpn/'+action,data=raw,
                headers={'Content-Type':'application/json','X-Pushok-Management':stamp+':'+signature},
                timeout=(3,20),allow_redirects=False)
            if not 200 <= reply.status_code < 300 and reply.status_code < 400:
                raise HTTPException(503,'vpn_shop_unavailable')
            if reply.status_code>=400:
                try:
                    error=reply.json()
                    detail=error.get('detail') if isinstance(error,dict) else None
                except ValueError:
                    detail=None
                if isinstance(detail,dict) and detail.get('code')=='FLOW_IDENTITY_CONFLICT':
                    raise HTTPException(409,'vpn_account_conflict')
                if isinstance(detail,str) and detail in ('cabinet_stars_disabled','telegram_bot_token_not_configured','invoice_unavailable'):
                    raise HTTPException(reply.status_code,detail)
                if reply.status_code==409:
                    if action=='gift':
                        gift_codes={'gift_conflict':'vpn_gift_conflict',
                                    'premium_subscription':'vpn_gift_premium',
                                    'subscription_unavailable':'vpn_gift_subscription_unavailable',
                                    'vpn_order_issuing':'vpn_gift_order_issuing'}
                        code=detail.get('code') if isinstance(detail,dict) else detail
                        raise HTTPException(409,gift_codes.get(code,'vpn_gift_unavailable')
                                            if isinstance(code,str) else 'vpn_gift_unavailable')
                    raise HTTPException(409,'vpn_offer_changed')
                if reply.status_code==502 and action=='checkout':
                    raise HTTPException(502,'invoice_unavailable')
                if reply.status_code in (401,403,422):
                    raise HTTPException(503,'vpn_shop_integration')
            if action=='status' and reply.status_code==404:
                raise HTTPException(404,'vpn_order_missing')
            reply.raise_for_status()
            result=reply.json()
            if not isinstance(result,dict):
                raise HTTPException(503,'vpn_shop_unavailable')
            if action=='plans' and result.get('available') and result.get('issuer')=='cabinet':
                reason=None
                if not accepts_stars():
                    reason='Оплата Stars для VPN отключена в настройках.'
                else:
                    try: credentials()
                    except HTTPException: reason='Бот для приёма Stars пока не настроен.'
                if reason:result.update(available=False,unavailable_reason=reason)
            return JSONResponse(result,headers={'Cache-Control':'private, no-store'})
    except (ValueError,requests.RequestException):
        raise HTTPException(503,'vpn_shop_unavailable') from None


def router_for(current_user):
    router=APIRouter(prefix='/api/v1/vpn')
    @router.get('/plans')
    def plans(user=Depends(current_user)):
        return exchange('plans',user)
    @router.post('/checkout')
    def checkout(choice:Choice,user=Depends(current_user)):
        return exchange('checkout',user,choice=choice.model_dump(mode='json'))
    @router.get('/orders/{order_id}')
    def status(order_id:UUID,user=Depends(current_user)):
        return exchange('status',user,order_id=str(order_id))
    return router
