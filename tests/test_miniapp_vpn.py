from hashlib import sha256
from unittest import TestCase
from unittest.mock import patch, MagicMock
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import vpn_download, vpn_shop


class MiniAppVpn(TestCase):
    def test_payment_targets_only_current_cabinet_account(self):
        user={'id':UUID(int=1),'telegram_user_id':125}
        app=FastAPI(); app.include_router(vpn_shop.router_for(lambda:user))
        client=TestClient(app)
        choice={'plan':'month','amount':199,'plan_version':1,'policy_version':0,'request_id':str(UUID(int=2)),'accept_terms':True}
        with patch.object(vpn_shop,'exchange',return_value={'ok':True}) as exchange:
            self.assertEqual(client.post('/api/v1/vpn/checkout',json={**choice,'subject':str(UUID(int=3))}).status_code,422)
            exchange.assert_not_called()
            self.assertEqual(client.post('/api/v1/vpn/checkout',json=choice).status_code,200)
            exchange.assert_called_once_with('checkout',user,choice=choice)

    def test_apk_is_verified_and_served_as_attachment_with_cors(self):
        app=FastAPI();app.include_router(vpn_download.router);cli=TestClient(app)
        data=b'fixture-package';digest=sha256(data).hexdigest()
        name='Pushok-Flow-v2.0.4.apk'
        response=MagicMock();response.__enter__.return_value=response;response.iter_content.return_value=[data]
        url=vpn_download.RELEASES+'/download/v2.0.4/'+name
        with patch.object(vpn_download,'metadata',return_value=(name,url,digest,len(data))),patch.object(vpn_download.requests,'get',return_value=response) as get:
            vpn_download.package.cache_clear()
            self.assertEqual(cli.head('/api/v1/vpn/flow.apk').headers['content-length'],str(len(data)))
            get.assert_not_called()
            result=cli.get('/api/v1/vpn/flow.apk')
            self.assertEqual(result.content,data)
            self.assertIn('attachment',result.headers['content-disposition'])
            self.assertEqual(result.headers['access-control-allow-origin'],'https://web.telegram.org')
            with self.assertRaises(ValueError):vpn_download.package(url,'0'*64,len(data))
            vpn_download.package.cache_clear()
