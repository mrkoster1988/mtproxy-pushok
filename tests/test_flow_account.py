"""Flow grants use real temporary PostgreSQL and synthetic existing cabinet sessions.

No Telegram calls, real account credentials, provider requests or wallet mutation
are needed to exercise consent, session isolation and the transaction races.
"""
import hashlib
import hmac
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import test_postgres as fixture
from app import flow_account as flow

main = fixture.main
TEST_KEY = b'flow-synthetic-test-key-with-at-least-32-bytes'


class FlowProtocol(unittest.TestCase):
    def setUp(self):
        self.user = {'id': uuid4()}
        self.app = FastAPI()
        self.app.add_middleware(flow.FlowNoStoreMiddleware)
        self.app.include_router(flow.router_for(lambda: 'postgresql://unused', lambda: TEST_KEY, lambda: self.user))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        limiter = patch.object(flow, '_rate_limit')
        limiter.start()
        self.addCleanup(limiter.stop)

    def test_capabilities_and_all_routing_errors_are_not_cached(self):
        response = self.client.get('/api/v1/flow/capabilities')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'protocol_version': 1, 'scopes': ['profile:read', 'wallet:read'],
                                           'session_ttl_seconds': 43200})
        for response in (response, self.client.get('/api/v1/flow/missing'),
                         self.client.put('/api/v1/flow/device')):
            self.assertEqual(response.headers['cache-control'], 'private, no-store')
            self.assertEqual(response.headers['referrer-policy'], 'no-referrer')

    def test_deterministic_opaque_token_uses_distinct_domain(self):
        device = 'pfd_' + 'a' * 43
        token = flow.access_token(device, TEST_KEY)
        self.assertEqual(token, 'pfa_' + hmac.new(TEST_KEY, b'pushok-flow-access-v1\0' + device.encode(), hashlib.sha256).hexdigest())
        self.assertEqual(flow.access_token(device, TEST_KEY), token)
        self.assertNotEqual(flow.access_token(device + 'b', TEST_KEY), token)
        self.assertNotEqual(flow.access_token(device, b'other-key' * 8), token)
        self.assertNotIn(device, token)
        self.assertRegex(token, r'^pfa_[a-f0-9]{64}$')

    def test_legacy_display_names_fit_native_text_constraints(self):
        name = flow._display_name({'first_name': '  Те\x1bст\tимя', 'last_name': 'Фами\x7fлия\n',
                                   'username': 'private_username'})
        self.assertEqual(name, 'Те ст имя Фами лия')
        self.assertFalse(any(ord(char) < 32 or ord(char) == 127 for char in name))
        self.assertEqual(flow._display_name({'first_name': '\x1b\x7f', 'last_name': None}), 'Пользователь')
        name = flow._display_name({'first_name': '😀' * 500, 'last_name': '余' * 500})
        self.assertEqual(len(name), 160)
        self.assertLessEqual(len(name.encode('utf-16-le')) // 2, 512)
        self.assertEqual(flow._display_name({'first_name': '@private_username', 'username': 'private_username'}), 'Пользователь')

    def test_json_is_strict_bounded_and_errors_do_not_echo_inputs(self):
        inputs = [
            ('/device', b'{"scope":"admin"}', 400),
            ('/device', b'[]', 400),
            ('/device', b'{"x":NaN}', 400),
            ('/device/token', b'{"device_code":"private-value","device_code":"other-value"}', 400),
            ('/device/token', b'{"device_code":null}', 410),
            ('/device/token', b'{"device_code":"private-value"}', 410),
            ('/device/review', b'{"user_code":"short"}', 400),
            ('/device/decision', b'{"user_code":"23456789ABCD","decision":true}', 400),
            ('/device/decision', b'{"user_code":"23456789ABCD","decision":"approve","user_id":"other"}', 400),
            ('/logout', b'{"access_token":"private-value"}', 400),
            ('/device', b'{"x":"' + b'a' * 4096 + b'"}', 413),
            ('/device', b'{"x":"\xff"}', 400),
        ]
        with patch.object(flow, 'connect', side_effect=AssertionError('Validation must precede account DB access')):
            for path, body, status in inputs:
                with self.subTest(path=path, body_length=len(body)):
                    result = self.client.post('/api/v1/flow' + path, content=body,
                                              headers={'Content-Type': 'application/json'})
                    self.assertEqual(result.status_code, status, result.text)
                    self.assertEqual(set(result.json()), {'error'})
                    self.assertNotIn('private-value', result.text)
                    self.assertEqual(result.headers['cache-control'], 'private, no-store')
            for path in ('/device?scope=admin', '/capabilities?redirect_uri=https://example.test'):
                method = self.client.get if path.startswith('/capabilities') else self.client.post
                self.assertEqual(method('/api/v1/flow' + path).status_code, 400)
            self.assertEqual(self.client.post('/api/v1/flow/device', content='{}').status_code, 415)
            chunks = (b' ' * 1024 for _ in range(5))
            response = self.client.post('/api/v1/flow/device', content=chunks,
                                        headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 413)

    def test_cabinet_bearer_cannot_enter_flow_and_database_errors_are_safe(self):
        with patch.object(flow, 'connect', side_effect=AssertionError('Bad bearer must never reach profile SELECT')):
            for token in ('', 'Bearer cabinet.signature', 'Bearer pfd_' + 'a' * 43,
                          'Bearer pfa_' + 'a' * 63, 'Basic pfa_' + 'a' * 64):
                response = self.client.get('/api/v1/flow/me', headers={'Authorization': token})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {'error': 'unauthorized'})
        with patch.object(flow, 'connect', side_effect=psycopg.OperationalError('secret-sql-parameter')):
            result = self.client.post('/api/v1/flow/device', json={})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json(), {'error': 'service_unavailable'})
            self.assertNotIn('secret-sql-parameter', result.text)

    def test_cabinet_dependency_errors_are_sanitized(self):
        def unavailable():
            raise HTTPException(401, {'unexpected_credential': 'never-echo-this'})
        app = FastAPI()
        app.include_router(flow.router_for(lambda: 'unused', lambda: TEST_KEY, unavailable))
        with TestClient(app) as client:
            result = client.post('/api/v1/flow/device/review', json={'user_code': '23456789ABCD'})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.json(), {'error': 'unauthorized'})


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class FlowAccountIntegration(unittest.TestCase):
    verified_fixture = False
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request

    def setUp(self):
        main.app.dependency_overrides.clear()
        self.addCleanup(main.app.dependency_overrides.clear)
        key = patch.object(main, 'SESSION_SIGNING_KEY', TEST_KEY.decode())
        key.start()
        self.addCleanup(key.stop)
        environment = patch.dict(fixture.os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': '0'})
        environment.start()
        self.addCleanup(environment.stop)
        self.owner, self.other = uuid4(), uuid4()
        self.cabinet = {}
        with flow.connect(self.dsn) as conn:
            conn.execute('TRUNCATE flow_device_requests,flow_sessions,flow_rate_windows')
            for uid, role in ((self.owner, 'admin'), (self.other, 'advertiser')):
                conn.execute('''INSERT INTO users(id,telegram_user_id,role,first_name,last_name,username)
                    VALUES(%s,%s,%s,'Тест','Аккаунт','synthetic_private_username')''',
                    (uid, int(uuid4().hex[:12], 16), role))
                sid = uuid4()
                conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '12 hours')", (sid, uid))
                self.cabinet[uid] = {'Authorization': 'Bearer ' + main._create_session_token(uid, sid)}
            conn.execute("INSERT INTO verified_profiles(user_id,phone_number,phone_verified_at,telegram_environment) VALUES(%s,'+79990000001',now(),'production')", (self.owner,))

    def device(self):
        response = self.client.post('/api/v1/flow/device', json={})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        value = response.json()
        self.assertRegex(value['device_code'], r'^pfd_[A-Za-z0-9_-]{43}$')
        self.assertRegex(value['user_code'], r'^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{12}$')
        self.assertEqual((value['expires_in'], value['interval']), (300, 5))
        return value

    def decision(self, device, *, actor=None, action='approve', expected=200):
        return self.request('POST', '/flow/device/decision', expected,
                            json={'user_code': device['user_code'], 'decision': action},
                            headers=self.cabinet[actor or self.owner])

    def poll(self, device, *, expected=200):
        return self.request('POST', '/flow/device/token', expected, json={'device_code': device['device_code']})

    def allow_next_poll(self, device):
        # Move only the last-poll marker; never extend grant/session lifetimes.
        with flow.connect(self.dsn) as conn:
            conn.execute("UPDATE flow_device_requests SET last_polled_at=now()-interval '6 seconds' WHERE device_code_hash=%s",
                         (flow.digest(device['device_code']),))

    def authorize(self, *, actor=None):
        device = self.device()
        self.decision(device, actor=actor)
        token = self.poll(device)
        self.assertEqual(token['status'], 'authorized')
        self.assertEqual(token['scope'], ['profile:read', 'wallet:read'])
        self.assertEqual(token['token_type'], 'Bearer')
        self.assertTrue(43190 <= token['expires_in'] <= 43200)
        return device, token

    @staticmethod
    def bearer(token):
        return {'Authorization': 'Bearer ' + token['access_token']}

    def test_explicit_consent_read_only_wallet_and_bearer_separation(self):
        with flow.connect(self.dsn) as conn:
            before = conn.execute('SELECT count(*) AS n FROM users').fetchone()['n']
            conn.execute("INSERT INTO wallet_accounts(user_id,currency,balance) VALUES(%s,'RUB',-12345),(%s,'XTR',17)",
                         (self.owner, self.owner))
        device = self.device()
        review = self.request('POST', '/flow/device/review', json={'user_code': device['user_code']}, headers=self.cabinet[self.owner])
        self.assertEqual(review['status'], 'pending')
        self.assertEqual(set(review), {'client_name', 'user_code', 'scopes', 'expires_in', 'status'})
        self.assertEqual(self.poll(device)['status'], 'pending')
        response = self.client.post('/api/v1/flow/device/token', json={'device_code': device['device_code']})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error'], 'slow_down')
        self.assertTrue(1 <= int(response.headers['retry-after']) <= 5)
        with flow.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM flow_sessions').fetchone()['n'], 0)
        self.decision(device)
        self.allow_next_poll(device)
        token = self.poll(device)
        profile = self.request('GET', '/flow/me', headers=self.bearer(token))
        self.assertEqual(profile, {'id': str(self.owner), 'display_name': 'Тест Аккаунт', 'role': 'admin',
                                  'phone_verified': True, 'wallet': {'RUB': -12345, 'BONUS': 0, 'XTR': 17}})
        self.request('GET', '/me', 401, headers=self.bearer(token))
        self.request('GET', '/admin/control', 401, headers=self.bearer(token))
        self.request('GET', '/wallet', 401, headers=self.bearer(token))
        self.request('GET', '/flow/me', 401, headers=self.cabinet[self.owner])
        self.request('POST', '/flow/logout', 401, json={}, headers=self.cabinet[self.owner])
        with flow.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM users').fetchone()['n'], before)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM wallet_accounts WHERE user_id=%s', (self.owner,)).fetchone()['n'], 2)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM wallet_preferences WHERE user_id=%s', (self.owner,)).fetchone()['n'], 0)
            stored = conn.execute('SELECT row_to_json(d)::text AS value FROM flow_device_requests d').fetchone()['value']
            stored += conn.execute('SELECT row_to_json(s)::text AS value FROM flow_sessions s').fetchone()['value']
            self.assertNotIn(device['device_code'], stored)
            self.assertNotIn(token['access_token'], stored)
            self.assertIn(flow.digest(device['device_code']), stored)
            self.assertIn(flow.digest(token['access_token']), stored)
        self.request('POST', '/flow/logout', 204, json={}, headers=self.bearer(token))
        self.request('GET', '/flow/me', 401, headers=self.bearer(token))
        self.request('POST', '/flow/logout', 401, json={}, headers=self.bearer(token))
        self.poll(device, expected=410)
        self.request('GET', '/me', headers=self.cabinet[self.owner])

    def test_denial_same_account_idempotency_and_other_account_privacy(self):
        device = self.device()
        self.decision(device, action='deny')
        self.decision(device, action='deny')
        self.decision(device, action='approve', expected=409)
        self.decision(device, actor=self.other, action='deny', expected=404)
        self.request('POST', '/flow/device/review', 404, json={'user_code': device['user_code']}, headers=self.cabinet[self.other])
        own = self.request('POST', '/flow/device/review', json={'user_code': device['user_code']}, headers=self.cabinet[self.owner])
        self.assertEqual(own['status'], 'denied')
        self.assertEqual(self.poll(device, expected=403), {'error': 'access_denied'})

    def test_parallel_exchange_redelivers_one_token_without_extending_expiry(self):
        device = self.device()
        self.decision(device)
        self.decision(device)
        def poll(_):
            return self.client.post('/api/v1/flow/device/token', json={'device_code': device['device_code']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(poll, range(2)))
        self.assertCountEqual([r.status_code for r in results], [200, 429])
        first = next(r.json() for r in results if r.status_code == 200)
        with flow.connect(self.dsn) as conn:
            before = conn.execute('SELECT id,created_at,expires_at FROM flow_sessions').fetchall()
            deadline = conn.execute('SELECT expires_at FROM flow_device_requests').fetchone()['expires_at']
        self.assertEqual(len(before), 1)
        self.allow_next_poll(device)
        retry = self.poll(device)
        self.assertEqual(retry['access_token'], first['access_token'])
        self.assertLessEqual(retry['expires_in'], first['expires_in'])
        with flow.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT id,created_at,expires_at FROM flow_sessions').fetchall(), before)
            self.assertEqual(conn.execute('SELECT expires_at FROM flow_device_requests').fetchone()['expires_at'], deadline)
        self.allow_next_poll(device)
        with patch.object(main, 'SESSION_SIGNING_KEY', 'different-synthetic-key' * 3):
            self.poll(device, expected=410)
        self.request('GET', '/flow/me', headers=self.bearer(first))

    def test_parallel_accounts_cannot_both_approve_one_request(self):
        device = self.device()
        def decide(actor):
            return actor, self.client.post('/api/v1/flow/device/decision',
                json={'user_code': device['user_code'], 'decision': 'approve'}, headers=self.cabinet[actor])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, (self.owner, self.other)))
        self.assertCountEqual([result.status_code for _, result in results], [200, 404])
        winner = next(actor for actor, result in results if result.status_code == 200)
        token = self.poll(device)
        profile = self.request('GET', '/flow/me', headers=self.bearer(token))
        self.assertEqual(profile['id'], str(winner))

    def test_grant_expiry_prevents_redelivery_but_does_not_expire_session_early(self):
        device, token = self.authorize()
        with flow.connect(self.dsn) as conn:
            conn.execute("UPDATE flow_device_requests SET created_at=now()-interval '6 minutes',expires_at=now()-interval '1 minute'")
        self.poll(device, expected=410)
        self.request('GET', '/flow/me', headers=self.bearer(token))
        with flow.connect(self.dsn) as conn:
            conn.execute("UPDATE flow_sessions SET created_at=now()-interval '13 hours',expires_at=now()-interval '1 hour'")
        self.request('GET', '/flow/me', 401, headers=self.bearer(token))

    def test_blocking_rechecked_at_decision_exchange_and_profile(self):
        device = self.device()
        def blocked(value):
            with flow.connect(self.dsn) as conn:
                conn.execute('UPDATE users SET blocked_at=CASE WHEN %s THEN now() ELSE NULL END WHERE id=%s', (value, self.owner))
        blocked(True)
        self.decision(device, expected=401)
        blocked(False)
        self.decision(device)
        blocked(True)
        self.poll(device, expected=403)
        blocked(False)
        token = self.poll(device)
        blocked(True)
        self.request('GET', '/flow/me', 401, headers=self.bearer(token))
        self.poll(device, expected=403)

    def test_revocation_exchange_race_never_resurrects_session(self):
        device, token = self.authorize()
        self.allow_next_poll(device)
        def action(kind):
            if kind == 'logout':
                return kind, self.client.post('/api/v1/flow/logout', json={}, headers=self.bearer(token))
            return kind, self.client.post('/api/v1/flow/device/token', json={'device_code': device['device_code']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = dict(pool.map(action, ('logout', 'poll')))
        self.assertEqual(results['logout'].status_code, 204, results['logout'].text)
        self.assertIn(results['poll'].status_code, (200, 410), results['poll'].text)
        if results['poll'].status_code == 200:
            self.assertEqual(results['poll'].json()['access_token'], token['access_token'])
        self.poll(device, expected=410)
        self.request('GET', '/flow/me', 401, headers=self.bearer(token))
        with flow.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM flow_sessions').fetchone()['n'], 0)

    def test_client_and_global_limits_ignore_forwarded_headers_and_bound_state(self):
        limits = {**flow.RATE_LIMITS, 'device': (1, 2)}
        with patch.object(flow, 'RATE_LIMITS', limits):
            first = self.client.post('/api/v1/flow/device', json={}, headers={'X-Forwarded-For': '198.51.100.1'})
            self.assertEqual(first.status_code, 201, first.text)
            second = self.client.post('/api/v1/flow/device', json={}, headers={'X-Forwarded-For': '198.51.100.2'})
            self.assertEqual(second.status_code, 429, second.text)
            self.assertTrue(1 <= second.json()['interval'] <= 60)
            with self.assertRaises(flow.FlowError) as caught:
                flow._rate_limit(self.dsn, 'another-socket-address', 'device')
            self.assertEqual(caught.exception.status, 429)
            with flow.connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM flow_rate_windows WHERE bucket=%s', ('device',)).fetchone()['n'], 2)
                conn.execute("INSERT INTO flow_rate_windows VALUES('device','stale',now()-interval '3 minutes',1)")
            with self.assertRaises(flow.FlowError):
                flow._rate_limit(self.dsn, 'yet-another-socket-address', 'device')
            with flow.connect(self.dsn) as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM flow_rate_windows WHERE client_key='stale'").fetchone())

    def test_grant_and_session_caps_are_enforced_without_partial_issuance(self):
        with patch.object(flow, 'MAX_DEVICE_REQUESTS', 1):
            self.device()
            self.request('POST', '/flow/device', 429, json={})
        with flow.connect(self.dsn) as conn:
            conn.execute('TRUNCATE flow_device_requests,flow_sessions,flow_rate_windows')
        with patch.object(flow, 'MAX_SESSIONS_PER_USER', 1):
            self.authorize()
            device = self.device()
            self.decision(device)
            self.poll(device, expected=429)
        with patch.object(flow, 'MAX_SESSIONS', 1):
            other = self.device()
            self.decision(other, actor=self.other)
            self.poll(other, expected=429)
        with flow.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM flow_sessions').fetchone()['n'], 1)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM flow_device_requests WHERE session_id IS NOT NULL').fetchone()['n'], 1)

    def test_migration_repeats_and_session_scope_cannot_be_escalated(self):
        device, token = self.authorize()
        migration = (Path(__file__).resolve().parents[1] / 'database/075_flow_account.sql').read_text()
        with flow.connect(self.dsn) as conn:
            conn.execute(migration)
            conn.execute(migration)
        with self.assertRaises(psycopg.errors.CheckViolation):
            with flow.connect(self.dsn) as conn:
                conn.execute("UPDATE flow_sessions SET scopes=ARRAY['profile:read','wallet:read','admin']")
        self.request('GET', '/flow/me', headers=self.bearer(token))


if __name__ == '__main__':
    unittest.main()
