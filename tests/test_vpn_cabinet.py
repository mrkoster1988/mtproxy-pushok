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
        account = {'state':'active', 'online':True, 'received':12345, 'sent':99,
                   'expires_at':1900000000, 'last_seen':1800000000}
        mock = MagicMock()
        mock.__enter__.return_value.execute.return_value.fetchall.return_value = [
            {'id': other,'first_name':'Участник','last_name':None,'is_developer':False}]
        with patch.object(vpn_cabinet, 'observations', return_value={'accounts':{str(other):account},'sampled_at':1800000000}), patch.object(vpn_cabinet.psycopg,'connect',return_value=mock):
            data=client.get('/api/v1/vpn/dashboard').json()
            self.assertIsNone(data['personal'])
            self.assertNotIn('members',data)
            self.assertEqual(data['leaders'],[{'id':str(other),'name':'Участник'}])
            user['role']='admin'
            self.assertEqual(client.get('/api/v1/vpn/dashboard').json()['members'][0]['received'],12345)
