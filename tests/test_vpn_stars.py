import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import vpn_stars as shared
from bot.main import process_stars_update


class VpnStars(unittest.TestCase):
    def test_signed_invoice_and_bot_routes(self):
        app=FastAPI();app.include_router(shared.router);client=TestClient(app)
        key='fa'*32;oid=str(uuid4());raw=json.dumps({'order_id':oid,'days':30,'amount':156}).encode()
        stamp=str(int(time.time()))
        signature=hmac.digest(bytes.fromhex(key),shared.DOMAIN+stamp.encode()+b':invoice:'+raw,'sha256').hex()
        headers={'Content-Type':'application/json','X-Pushok-Management':stamp+':'+signature}
        url='/api/v1/shop/vpn-stars/invoice'
        with patch.dict('os.environ',{'FLOW_SHOP_BRIDGE_SECRET':key}),patch.object(shared,'credentials',return_value=('fixture',1)),patch.object(shared,'create_invoice_link',return_value='https://t.me/$fixture') as create,patch.object(shared,'is_test_environment',return_value=False),patch.object(shared,'stars_invoices_enabled',return_value=True):
            self.assertEqual(client.post(url,content=raw,headers=headers).json(),{'url':'https://t.me/$fixture'})
            self.assertEqual(create.call_args.args[1].invoice_payload,'shop-vpn:'+oid)
            self.assertEqual(client.post(url,content=raw+b' ',headers=headers).status_code,403)
            with patch.object(shared,'is_test_environment',return_value=True):
                self.assertEqual(client.post(url,content=raw,headers=headers).status_code,409)
            with patch.object(shared,'stars_invoices_enabled',return_value=False):
                self.assertEqual(client.post(url,content=raw,headers=headers).status_code,409)
        base={'invoice_payload':'shop-vpn:'+oid,'total_amount':156,'currency':'XTR'}
        internal=Mock(side_effect=[{'ok':True},{'status':'applied'},{'status':'recorded'}]);send=Mock()
        self.assertTrue(process_stars_update({'pre_checkout_query':{**base,'id':'qid','from':{'id':88}}},'t',internal,send))
        private={'from':{'id':88},'chat':{'type':'private','id':88},'date':int(stamp)}
        for k in ('successful_payment','refunded_payment'):
            self.assertTrue(process_stars_update({'message':{**private,k:{**base,'telegram_payment_charge_id':'receipt'}}},'t',internal,send))
        self.assertEqual([c.args[0] for c in internal.call_args_list],['vpn-stars/precheckout','vpn-stars/settle','vpn-stars/refund'])
        self.assertEqual(internal.call_args.args[1]['occurred_at'],int(stamp))
        self.assertEqual(send.call_count,1)
