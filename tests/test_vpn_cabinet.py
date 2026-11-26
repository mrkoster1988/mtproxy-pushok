import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import vpn_cabinet


class VpnVisibility(unittest.TestCase):
    def test_only_admin_can_read_another_members_traffic_and_expiry(self):
        uid, other = UUID(int=1), UUID(int=2)
        user = {'id': uid, 'role':'advertiser'}
        app = FastAPI()
        app.include_router(vpn_cabinet.router_for(lambda: 'test', lambda: user))
        client = TestClient(app)
        account = {'state':'active','plan_notice':{'created':1800000000,'delivery':'waiting_chat','text':'Тариф изменён'}, 'tariff_name':'Семейный', 'online':True, 'received':12345, 'sent':99,
                   'expires_at':1900000000, 'last_seen':1800000000, 'endpoint_ip':'192.0.2.4',
                   'tunnel_ips':['10.0.0.4']}
        mock = MagicMock()
        mock.__enter__.return_value.execute.return_value.fetchall.return_value = [
            {'id': other,'first_name':'Участник','last_name':None,'phone_verified':True,'is_developer':False}]
        with patch.object(vpn_cabinet, 'observations', return_value={'accounts':{str(other):account},'sampled_at':1800000000}), patch.object(vpn_cabinet.psycopg,'connect',return_value=mock):
            data=client.get('/api/v1/vpn/dashboard').json()
            self.assertIsNone(data['personal'])
            self.assertNotIn('members',data)
            self.assertNotIn('leaders',data)
            self.assertFalse(data['can_manage'])
            mock.assert_not_called()
            user['id'] = other
            for role in ('advertiser','operator','moderator'):
                user['role']=role
                personal=client.get('/api/v1/vpn/dashboard').json()['personal']
                self.assertEqual(personal['received'],12345)
                self.assertEqual(personal['plan_notice'],account['plan_notice'])
                for key in ('endpoint_ip','tunnel_ips','country_code','city'):
                    self.assertNotIn(key,personal)
            user['id'] = uid
            user['role']='admin'
            staff=client.get('/api/v1/vpn/dashboard').json()['members'][0]
            self.assertEqual(staff['received'],12345)
            self.assertEqual(staff['endpoint_ip'],'192.0.2.4')
            self.assertIn('country_code',staff)
            self.assertIn('city',staff)
            self.assertTrue(staff['phone_verified'])
            self.assertEqual(staff['tariff_name'],'Семейный')
            self.assertEqual(staff['plan_notice'],account['plan_notice'])

    def test_management_checks_current_role_before_forwarding(self):
        user = {'id': UUID(int=1), 'role':'operator'}
        app = FastAPI()
        app.include_router(vpn_cabinet.router_for(lambda: 'test', lambda: user))
        client = TestClient(app)
        url = '/api/v1/vpn/users/'+str(UUID(int=2))+'/management'
        data = {'action':'limit','request_id':str(UUID(int=3)), 'subscription_id':'s', 'before':4,'value':2}
        with patch.object(vpn_cabinet, 'change_subscription') as send:
            for role in ('advertiser','moderator','operator'):
                user['role'] = role
                self.assertEqual(client.post(url,json=data).status_code,403)
            send.assert_not_called()
            user['role'] = 'admin'
            self.assertEqual(client.post(url,json=data).json(),{'ok':True})
            self.assertEqual(send.call_args.args[:2], (user['id'], UUID(int=2)))


    def test_shop_issuing_order_is_identified_in_admin_response(self):
        actor, subject = UUID(int=1), UUID(int=2)
        change = vpn_cabinet.Change(action='restore', subscription_id='wg', request_id=UUID(int=3), before=1, value=1)
        with patch.object(vpn_cabinet, 'bridge_key', return_value=b'x'*32), patch.object(vpn_cabinet.requests, 'Session') as session:
            response = session.return_value.__enter__.return_value.post.return_value
            response.status_code = 409
            detail = {'code':'vpn_order_issuing','order_id':'test-order-35'}
            response.json.return_value = {'detail': {**detail, 'extra': 'omitted'}}
            with self.assertRaises(vpn_cabinet.HTTPException) as conflict:
                vpn_cabinet.change_subscription(actor, subject, change)
            self.assertEqual(conflict.exception.detail, detail)


class VpnGeolocation(unittest.TestCase):
    def test_local_city_lookup_and_private_addresses(self):
        from app import vpn_geo
        vpn_geo.location.cache_clear()
        with patch.object(vpn_geo,'database') as database:
            for address in ('10.0.0.1','::1','invalid',None):
                self.assertEqual(vpn_geo.location(address),(None,None))
            database.assert_not_called()
            database.return_value.get.return_value={'country_code':'RU','city':'Moscow (district)'}
            self.assertEqual(vpn_geo.location('::ffff:77.88.8.8'),('RU','Москва'))
            database.assert_called_once_with(4)
            database.return_value.get.return_value={'country_code':'CH','city':'Zürich'}
            self.assertEqual(vpn_geo.location('2001:4860::123'),('CH','Zürich'))
            database.assert_called_with(6)
        vpn_geo.location.cache_clear()
