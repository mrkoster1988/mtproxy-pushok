"""Attribution at real registration entry points in an isolated PostgreSQL database."""
import hashlib
import hmac
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

import psycopg
import test_postgres as fixture
from test_miniapp_auth import signed_data


@unittest.skipUnless(fixture._approved_test_postgres(), 'Требуется тестовый PostgreSQL')
class DirectRegistrationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request
    token = 'fixture-token'

    def setUp(self):
        self.base = int(uuid4().hex[:12], 16)
        self.owner, self.inviter = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE users SET is_owner=false WHERE is_owner")
            conn.execute("""INSERT INTO users(id,telegram_user_id,role,is_owner) VALUES
                (%s,%s,'admin',true),(%s,%s,'admin',false)""",
                (self.owner, self.base, self.inviter, self.base+1))
            conn.execute('UPDATE users SET developer_granted=true WHERE id=%s', (self.inviter,))
            self.code = conn.execute("INSERT INTO growth_profiles(user_id) VALUES(%s) RETURNING code",
                                     (self.inviter,)).fetchone()[0]
            conn.execute('UPDATE growth_settings SET referrals_enabled=true')
        for env in (patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED':'1'}),
                    patch.object(fixture.main, 'TELEGRAM_BOT_TOKEN', self.token),
                    patch.object(fixture.main, 'SESSION_SIGNING_KEY', 'x'*64)):
            env.start()
            self.addCleanup(env.stop)

    def mini(self, tg, start='', cookie=''):
        data = signed_data(self.token, query_id=uuid4().hex, start_param=start,
                           user=json.dumps({'id':tg, 'first_name':'Тест'}))
        self.request('POST', '/auth/telegram/miniapp', json={'init_data':data},
                     headers={'Cookie':'__Host-pushok_invite='+cookie} if cookie else {})

    def widget(self, tg, code=''):
        data = dict(id=tg, first_name='Тест', auth_date=int(time.time()))
        check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
        data['hash'] = hmac.new(hashlib.sha256(self.token.encode()).digest(),
                                check.encode(), hashlib.sha256).hexdigest()
        self.request('POST', '/auth/telegram', json=data, headers={'Cookie':'__Host-pushok_invite='+code})

    def attribution(self, tg):
        with psycopg.connect(self.dsn) as conn:
            return conn.execute("""SELECT r.inviter_user_id,r.rewards_enabled FROM growth_referrals r
                JOIN users u ON u.id=r.referred_user_id WHERE u.telegram_user_id=%s""", (tg,)).fetchall()

    def test_direct_signed_cookie_bot_and_repeat(self):
        self.mini(self.base+2, 'vpn')
        self.mini(self.base+2, 'r_'+self.code)
        self.assertEqual(self.attribution(self.base+2), [(self.owner, True)])
        self.mini(self.base+3, 'r_'+self.code, 'BADCODE2')
        self.widget(self.base+4, self.code)
        with patch.dict(os.environ, {'BOT_INTERNAL_SECRET':'b'*64}):
            self.request('POST','/internal/bot/referral',json={'telegram_user_id':self.base+5,'code':self.code},
                         headers={'X-Pushok-Bot-Secret':'b'*64})
        self.mini(self.base+5)
        for tg in (self.base+3,self.base+4,self.base+5):
            self.assertEqual(self.attribution(tg),[(self.inviter,True)])
        self.mini(self.base)
        self.mini(self.base+1)
        self.assertFalse(self.attribution(self.base))
        self.assertFalse(self.attribution(self.base+1))

    def test_pause_records_direct_without_reward_and_preserves_real_link(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE growth_settings SET referrals_enabled=false')
        self.widget(self.base+2)
        self.mini(self.base+3, 'r_'+self.code)
        self.assertEqual(self.attribution(self.base+2), [(self.owner,False)])
        self.assertFalse(self.attribution(self.base+3))

    def test_owner_appointed_after_registration(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
        self.mini(self.base+2)
        self.mini(self.base+3, 'ref_'+self.code)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s', (self.owner,))
        self.assertEqual(self.attribution(self.base+2), [(self.owner,True)])
        self.assertEqual(self.attribution(self.base+3), [(self.inviter,True)])

    def test_simultaneous_first_visits_credit_one_invitee(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(self.mini, [self.base+2]*2))
        self.assertEqual(self.attribution(self.base+2), [(self.owner,True)])
        with psycopg.connect(self.dsn) as conn:
            events = conn.execute("""SELECT count(*) FROM audit_events e JOIN users u ON u.id=e.actor_user_id
                WHERE u.telegram_user_id=%s AND e.details_json->>'source'='direct_registration'""", (self.base+2,)).fetchone()
            self.assertEqual(events, (1,))

    def test_backfill_once_preserves_existing_invites_self_and_historical_rewards(self):
        from app.growth import claim_referral
        from psycopg.rows import dict_row
        sql = (fixture.ROOT/'database/085_attribute_existing_registrations.sql').read_text()
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute('UPDATE growth_settings SET direct_registrations_imported_at=NULL')
            unbound = conn.execute("""INSERT INTO users(telegram_user_id,created_at)
                VALUES(%s,now()-interval '1 year') RETURNING id""", (self.base+2,)).fetchone()['id']
            invited = conn.execute('INSERT INTO users(telegram_user_id) VALUES(%s) RETURNING id',
                                   (self.base+3,)).fetchone()['id']
            service = conn.execute('INSERT INTO users(telegram_user_id) VALUES(%s) RETURNING id',
                                   (-self.base,)).fetchone()['id']
            claim_referral(conn, invited, self.code)
            conn.execute(sql)
            record = conn.execute("""SELECT r.inviter_user_id,r.created_at>u.created_at AS assigned_today
                FROM growth_referrals r JOIN users u ON u.id=r.referred_user_id WHERE u.id=%s""", (unbound,)).fetchone()
            self.assertEqual(record, dict(inviter_user_id=self.owner,assigned_today=True))
            self.assertFalse(conn.execute('SELECT 1 FROM growth_referrals WHERE referred_user_id IN (%s,%s)',
                                          (self.owner,service)).fetchone())
            self.assertEqual(conn.execute('SELECT inviter_user_id FROM growth_referrals WHERE referred_user_id=%s',
                                          (invited,)).fetchone()['inviter_user_id'], self.inviter)
            self.assertFalse(conn.execute('SELECT 1 FROM wallet_entries WHERE user_id=%s', (self.owner,)).fetchone())
            conn.execute('DELETE FROM growth_referrals WHERE referred_user_id=%s', (unbound,))
            conn.execute(sql)
            self.assertFalse(conn.execute('SELECT 1 FROM growth_referrals WHERE referred_user_id=%s', (unbound,)).fetchone())
