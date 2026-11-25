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
            if reply.status_code==409:
                raise HTTPException(409,'vpn_offer_changed')
            if action=='status' and reply.status_code==404:
                raise HTTPException(404,'vpn_order_missing')
            reply.raise_for_status()
            return JSONResponse(reply.json(),headers={'Cache-Control':'private, no-store'})
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
