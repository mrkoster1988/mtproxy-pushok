"""Матрица ролей, отзыв сессий и запрет обхода блокировки через Telegram-бота."""
import json
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from test_miniapp_auth import signed_data
from app import main, wallet_ledger


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class UserAccessIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.ids={r:uuid4() for r in ('admin','operator','advertiser','target')}
        self.tgs={r:int(uid.hex[:12],16) for r,uid in self.ids.items()}
        with psycopg.connect(self.dsn) as conn:
            for role,uid in self.ids.items():
                conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,%s,%s)',(uid,self.tgs[role],'advertiser' if role=='target' else role,role))
        self.act('admin')
        self.addCleanup(main.app.dependency_overrides.clear)

    def act(self,role):
        self.actor={'id':self.ids[role],'role':role}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor

    def role(self,target,role,status=200):
        return self.request('PUT','/admin/team',status,json={'telegram_user_id':self.tgs[target],'role':role})

    def block(self,target,blocked,status=200):
        return self.request('PUT','/admin/team/access',status,json={'telegram_user_id':self.tgs[target],'blocked':blocked})

    def test_developer_assignment_requires_a_developer_and_preserves_installation_owner(self):
        self.role('target','developer',403)
        self.act('operator');self.role('target','developer',403)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE users SET is_owner=true WHERE id=%s",(self.ids['admin'],))
        self.addCleanup(self.clear_developer_fixture)
        self.act('admin');self.role('target','developer')
        with psycopg.connect(self.dsn) as conn:
            row=conn.execute("SELECT role,is_owner,is_developer FROM users WHERE id=%s",(self.ids['target'],)).fetchone()
            self.assertEqual(row,('admin',False,True))
            sid=uuid4()
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,self.ids['target']))
        main.app.dependency_overrides.clear()
        headers={'Authorization':'Bearer '+main._create_session_token(self.ids['target'],sid)}
        self.assertTrue(self.request('GET','/me',headers=headers)['is_developer'])
        self.request('GET','/admin/proxy-gateway',headers=headers)
        self.request('PUT','/admin/team',403,headers=headers,json={'telegram_user_id':self.tgs['admin'],'role':'advertiser'})
        self.request('PUT','/admin/team',headers=headers,json={'telegram_user_id':self.tgs['operator'],'role':'developer'})
        self.act('admin');self.role('target','admin')
        self.act('target');self.actor['role']='admin'
        self.role('operator','admin',403);self.block('operator',True,403)
        main.app.dependency_overrides.clear()
        self.request('GET','/me',401,headers=headers)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT is_developer FROM users WHERE id=%s",(self.ids['target'],)).fetchone(),(False,))
            self.assertEqual(conn.execute("SELECT count(*) FROM users WHERE is_owner").fetchone(),(1,))

    def clear_developer_fixture(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE users SET is_owner=false WHERE id=%s",(self.ids['admin'],))

    def test_admin_and_operator_matrix(self):
        for role in ('admin','operator','advertiser'):self.role('target',role)
        self.act('operator')
        self.request('GET','/admin/users')
        self.role('target','operator');self.role('target','advertiser')
        self.role('target','admin',403)
        for role in ('operator','advertiser','admin'):self.role('admin',role,403)
        self.block('admin',True,403);self.block('admin',False,403)
        self.block('target',True);self.block('target',False)
        self.role('operator','advertiser',409);self.block('operator',True,409)
        self.act('advertiser');self.role('target','operator',403);self.block('target',True,403)
        self.act('admin');self.block('admin',True,409)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT role FROM users WHERE id=%s',(self.ids['target'],)).fetchone()[0],'advertiser')

    def test_block_revokes_session_login_and_autopay_but_keeps_money(self):
        uid=self.ids['target'];sid=uuid4()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
            conn.execute('INSERT INTO wallet_preferences(user_id,auto_pay,per_campaign_limit) VALUES(%s,true,100000)',(uid,))
            wallet_ledger.post(conn,uid,'BONUS',10000,'manual_credit','access:'+str(uuid4()),'Тест',actor=self.ids['admin'])
        with patch.object(main,'SESSION_SIGNING_KEY','test-only-signing-key-for-access-136'),patch.object(main,'TELEGRAM_BOT_TOKEN','fixture-token'),patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'1'}):
            token=main._create_session_token(uid,sid)
            self.act('operator');self.block('target',True)
            main.app.dependency_overrides.clear()
            self.request('GET','/me',401,headers={'Authorization':'Bearer '+token})
            raw=signed_data(user=json.dumps({'id':self.tgs['target'],'first_name':'Получатель'}),query_id=str(uuid4()))
            self.assertEqual(self.request('POST','/auth/telegram/miniapp',403,json={'init_data':raw})['detail'],'user_blocked')
            self.act('operator');self.block('target',False)
            main.app.dependency_overrides.clear()
            self.request('GET','/me',401,headers={'Authorization':'Bearer '+token})
            login=self.request('POST','/auth/telegram/miniapp',json={'init_data':raw})
            self.request('GET','/me',headers={'Authorization':'Bearer '+login['access_token']})
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'",(uid,)).fetchone()[0],10000)
            self.assertFalse(conn.execute('SELECT auto_pay FROM wallet_preferences WHERE user_id=%s',(uid,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_events WHERE actor_user_id=%s AND event_type='user_access_changed'",(self.ids['operator'],)).fetchone()[0],2)

    def test_stale_actor_and_bot_cannot_bypass_block(self):
        self.block('operator',True)
        self.act('operator');self.role('target','operator',403);self.block('target',True,403)
        self.act('admin');self.block('target',True)
        with patch.dict(os.environ,{'DATABASE_URL':self.dsn,'BOT_INTERNAL_SECRET':'a'*64}):
            headers={'X-Pushok-Bot-Secret':'a'*64}
            self.request('POST','/internal/bot/operators/queue',403,headers=headers,json={'telegram_user_id':self.tgs['operator']})
            self.request('POST','/internal/bot/campaign-drafts',403,headers=headers,json={'telegram_user_id':self.tgs['target'],'channel_username':'access_test','request_key':str(uuid4())})
            self.request('POST','/internal/bot/referral',403,headers=headers,json={'telegram_user_id':self.tgs['target'],'code':'TESTCODE'})
        with psycopg.connect(self.dsn) as conn:
            self.assertFalse(conn.execute('SELECT 1 FROM campaigns WHERE advertiser_user_id=%s',(self.ids['target'],)).fetchone())
