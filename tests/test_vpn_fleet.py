"""Fleet permissions, signed requests and secret-safe upstream projections."""
import hmac
import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import vpn_fleet


def node():
    return {'id': 'server-1', 'name': 'Amsterdam', 'location': 'NL', 'domain': 'wg1.pushok.shop',
            'endpoint_ip': '8.8.8.8', 'dns_state': 'ready', 'state': 'disabled', 'health': 'ok',
            'traffic_verified': False, 'load_score': 0.25, 'capacity': 500, 'occupied': 10,
            'primary_count': 7, 'standby_count': 3, 'version': 1, 'checked_at': 1800000000,
            'api_checked_at': 1799999990, 'probe_status': 'stale', 'archived': False,
            'error_code': None, 'private_key': 'secret-never-export',
            'settings': {'api': 'wgdashboard', 'url': 'https://wg-panel.example', 'token': 'panel-secret',
                         'configuration': 'wg0', 'panel_timezone': 'Europe/Moscow',
                         'schedule_expiry': True, 'token_configured': True}}


class FleetAuthorization(TestCase):
    def setUp(self):
        self.user = {'id': UUID(int=1), 'role': 'advertiser'}
        app = FastAPI()
        app.include_router(vpn_fleet.router_for(lambda: self.user))
        self.client = TestClient(app)

    def test_reads_are_staff_only_before_shop_is_called(self):
        with patch.object(vpn_fleet, 'relay', return_value={}) as relay:
            for role in ('advertiser', 'operator', 'moderator'):
                self.user['role'] = role
                for url in ('/api/v1/vpn/fleet', '/api/v1/vpn/fleet/migrations/move-1'):
                    self.assertEqual(self.client.get(url).status_code, 403)
            relay.assert_not_called()
            self.user['role'] = 'admin'
            self.assertEqual(self.client.get('/api/v1/vpn/fleet').status_code, 200)
            self.user.update(role='advertiser', is_developer=True)
            self.assertEqual(self.client.get('/api/v1/vpn/fleet').status_code, 200)

    def test_mutations_require_owner_or_developer_flags(self):
        request = {'request_id': str(UUID(int=2)), 'payload': {'id': 'server-1'}}
        with patch.object(vpn_fleet, 'relay', return_value={}) as relay:
            for role in ('advertiser', 'operator', 'moderator', 'admin', 'developer'):
                self.user['role'] = role
                for action in vpn_fleet.SCHEMAS:
                    self.assertEqual(self.client.post('/api/v1/vpn/fleet/' + action, json=request).status_code, 403)
            relay.assert_not_called()
            self.user['is_owner'] = True
            self.assertEqual(self.client.post('/api/v1/vpn/fleet/check', json=request).status_code, 200)
            relay.assert_called_once_with('check', self.user, {'id': 'server-1'}, UUID(int=2))

    def test_caller_cannot_override_actor_or_send_unvalidated_payload(self):
        self.user['is_developer'] = True
        with patch.object(vpn_fleet, 'relay') as relay:
            request = {'request_id': str(UUID(int=2)), 'payload': {'id': 'server-1', 'actor': 'victim'}}
            reply = self.client.post('/api/v1/vpn/fleet/check', json=request)
            self.assertEqual(reply.status_code, 422)
            self.assertEqual(reply.json()['detail'], 'wg_fleet_invalid_input')
            request['payload'] = {'id': 'server-1', 'version': 1, 'state': 'active', 'settings': {'token': 'do-not-echo'}}
            reply = self.client.post('/api/v1/vpn/fleet/state', json=request)
            self.assertEqual(reply.status_code, 422)
            self.assertNotIn('do-not-echo', reply.text)
            for envelope in ({'payload': {'settings': {'token': 'do-not-echo'}}},
                             {'request_id': 'invalid', 'payload': {}, 'token': 'do-not-echo'},
                             ['do-not-echo'], None):
                reply = self.client.post('/api/v1/vpn/fleet/create', json=envelope)
                self.assertEqual(reply.status_code, 422)
                self.assertEqual(reply.json()['detail'], 'wg_fleet_invalid_input')
                self.assertNotIn('do-not-echo', reply.text)
            relay.assert_not_called()

    def test_create_connection_and_optional_token_update(self):
        create = {'name': 'Amsterdam', 'location': 'NL', 'capacity': 500, 'endpoint_ip': '8.8.8.8',
                  'settings': {'url': 'https://wg-panel.example/', 'token': 'panel-secret-' * 3, 'configuration': 'wg0'}}
        payload = vpn_fleet.payload_for('create', create)
        self.assertEqual(payload['settings']['token'], 'panel-secret-' * 3)
        self.assertEqual(payload['settings']['url'], 'https://wg-panel.example')
        update = {**create, 'id': 'server-1', 'version': 1}
        del update['settings']['token']
        updated = vpn_fleet.payload_for('update', update)
        self.assertNotIn('token', updated['settings'])
        update['endpoint_ip'] = ''
        self.assertNotIn('endpoint_ip', vpn_fleet.payload_for('update', update))
        for ip in ('127.0.0.1', '10.0.0.1', 'ff00::1', 'https://8.8.8.8'):
            with self.assertRaises(HTTPException):
                vpn_fleet.payload_for('update', {**update, 'endpoint_ip': ip})
        for url in ('https://user:secret@panel.example', 'https://panel.example?token=secret', 'file:///etc/passwd'):
            with self.assertRaises(HTTPException):
                vpn_fleet.payload_for('create', {**create, 'settings': {**create['settings'], 'token': 'panel-secret-' * 3, 'url': url}})


class FleetBridge(TestCase):
    def setUp(self):
        self.user = {'id': UUID(int=1), 'role': 'advertiser', 'is_developer': True}
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.reply = self.session.post.return_value
        self.reply.status_code = 200

    def call(self, action='snapshot', payload=None, request_id=None):
        with patch.dict(vpn_fleet.os.environ, {'FLOW_SHOP_BRIDGE_SECRET': 'ab' * 32}), \
                patch.object(vpn_fleet.time, 'time', return_value=1800000000), \
                patch.object(vpn_fleet.requests, 'Session', return_value=self.session):
            return vpn_fleet.relay(action, self.user, payload, request_id)

    def test_signature_binds_actor_role_action_body_and_idempotency_key(self):
        self.reply.json.return_value = {'node': node()}
        result = self.call('check', {'id': 'server-1'}, UUID(int=2))
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], vpn_fleet.BRIDGE_URL)
        body = kwargs['data']
        proof = kwargs['headers']['X-Pushok-Management']
        self.assertEqual(proof, '1800000000:' + hmac.digest(bytes.fromhex('ab' * 32),
                         vpn_fleet.DOMAIN + b'1800000000:' + body, 'sha256').hex())
        sent = json.loads(body)
        self.assertEqual(sent['actor'], str(self.user['id']))
        self.assertEqual(sent['actor_role'], 'developer')
        self.assertEqual(sent['request_id'], str(UUID(int=2)))
        self.assertEqual(sent['action'], 'check')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertFalse(self.session.trust_env)
        self.assertEqual(result.headers['cache-control'], 'private, no-store')
        self.assertFalse(json.loads(result.body)['node']['traffic_verified'])
        self.assertEqual(json.loads(result.body)['node']['api_checked_at'], 1799999990)
        self.assertEqual(json.loads(result.body)['node']['checked_at'], 1800000000)
        self.assertEqual(json.loads(result.body)['node']['probe_status'], 'stale')

    def test_snapshot_drops_all_tokens_and_admin_connection_settings(self):
        snapshot = {'nodes': [node()], 'operations': [], 'policy': {'version': 1, 'enabled': False, 'standby_count': 1}}
        self.reply.json.return_value = snapshot
        data = json.loads(self.call().body)
        self.assertTrue(data['can_manage'])
        self.assertTrue(data['nodes'][0]['settings']['token_configured'])
        self.assertNotIn('panel-secret', json.dumps(data))
        self.assertNotIn('secret-never-export', json.dumps(data))
        self.user.update(is_developer=False, role='admin')
        data = json.loads(self.call().body)
        self.assertFalse(data['can_manage'])
        self.assertNotIn('settings', data['nodes'][0])
        self.assertEqual(json.loads(self.session.post.call_args.kwargs['data'])['actor_role'], 'admin')

    def test_accepted_migration_stays_pending_and_errors_are_sanitized(self):
        self.reply.status_code = 202
        self.reply.json.return_value = {'operation': {'id': 'move-1', 'source_server_id': 'server-1',
            'state': 'queued', 'total': 7, 'completed': 0, 'failed': 0, 'error': 'token=do-not-echo'}}
        result = self.call('drain', {'id': 'server-1'}, UUID(int=2))
        self.assertEqual(result.status_code, 202)
        self.assertEqual(json.loads(result.body)['operation']['state'], 'queued')
        self.assertEqual(json.loads(result.body)['operation']['completed'], 0)
        self.assertNotIn('do-not-echo', result.body.decode())
        for status, detail, expected in ((409, 'wg_fleet_changed', 'wg_fleet_changed'),
                                         (503, 'Connection failed: token=secret', 'wg_fleet_change_unconfirmed'),
                                         (403, 'wg_fleet_management_disabled', 'wg_fleet_management_disabled'),
                                         (503, 'Управление серверами из Mini App не настроено.', 'wg_fleet_management_disabled'),
                                         (409, 'Сначала подтвердите DNS WireGuard.', 'wg_fleet_dns_not_ready'),
                                         (409, 'RemoteRevokeUnconfirmed', 'wg_fleet_remote_revoke_unconfirmed'),
                                         (401, 'Недействительный запрос управления.', 'wg_fleet_integration')):
            self.reply.status_code = status
            self.reply.json.return_value = {'detail': detail}
            with self.assertRaises(HTTPException) as error:
                self.call('drain', {'id': 'server-1'}, UUID(int=2))
            self.assertEqual(error.exception.status_code, status)
            self.assertEqual(error.exception.detail, expected)

    def test_redirect_malformed_and_unconfirmed_ok_are_failures(self):
        for status, result in ((302, {'node': node()}), (200, []), (200, {'ok': True})):
            self.reply.status_code = status
            self.reply.json.return_value = result
            with self.assertRaises(HTTPException) as error:
                self.call('create', {}, UUID(int=2))
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(error.exception.detail, 'wg_fleet_change_unconfirmed')

    def test_projection_removes_credentials_and_query_from_legacy_panel_url(self):
        info = vpn_fleet.safe_settings({'url': 'https://user:secret@panel.example:443/path?token=hidden#secret'})
        self.assertEqual(info['url'], 'https://panel.example:443/path')
        self.assertNotIn('secret', json.dumps(info))
