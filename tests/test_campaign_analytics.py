"""Ссылка и Telegram-вступления: разные источники, идемпотентность и приватность."""
import html
import os
import re
import time
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4
from fastapi.testclient import TestClient
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import campaign_analytics as analytics,wallet_topups as topups
from bot.campaign_analytics import member_payload


def event(**changes):
    value={'chat_member':{'chat':{'id':-100123},'date':int(time.time()),
           'old_chat_member':{'status':'left'},'new_chat_member':{'status':'member','user':{'id':999,'is_bot':False}},
           'invite_link':{'invite_link':'https://t.me/+abcdeFGHI123'}}}
    value['chat_member'].update(changes);return value


class AnalyticsContracts(unittest.TestCase):
    def test_only_new_human_members_and_own_link_shape(self):
        value=event();self.assertEqual(member_payload(value),analytics.member_payload(value))
        self.assertEqual(member_payload(value)['telegram_user_id'],999)
        for change in ({'old_chat_member':{'status':'member'}},{'new_chat_member':{'status':'left'}},
                       {'new_chat_member':{'status':'member','user':{'id':999,'is_bot':True}}},
                       {'invite_link':{'invite_link':'https://evil.test/'}},{'invite_link':{}}):
            self.assertIsNone(member_payload(event(**change)))
        self.assertIsNone(member_payload({}))

    def test_anonymous_report_and_bot_auth_before_database(self):
        app=fixture.main.app
        app.dependency_overrides.clear()
        with TestClient(app) as client,patch.object(analytics.psycopg,'connect') as connect:
            self.assertEqual(client.get('/api/v1/campaigns/'+str(uuid4())+'/analytics').status_code,401)
            with patch.dict(os.environ,{'BOT_INTERNAL_SECRET':'e'*64}):
                payload=member_payload(event())
                self.assertEqual(client.post('/api/v1/internal/bot/analytics/join',json=payload).status_code,401)
            connect.assert_not_called()

    def test_browser_identity_is_signed(self):
        with patch.dict(os.environ,{'SESSION_SIGNING_KEY':'s'*64}):
            raw='a'*32+'.'+analytics.digest('visitor:'+'a'*32)
            self.assertEqual(analytics.visitor(raw),'a'*32)
            self.assertIsNone(analytics.visitor(raw[:-1]+'x'));self.assertIsNone(analytics.visitor('bad'))


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class AnalyticsIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    setUp=wallet_fixture.WalletIntegration.setUp
    campaign=wallet_fixture.WalletIntegration.campaign

    def start(self):
        self.cid=self.campaign();self.path='/api/v1/campaigns/'+str(self.cid)+'/analytics'
        self.web=TestClient(fixture.main.app,base_url='https://testserver');self.addCleanup(self.web.close)
        self.env=patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'123456:analytics_test','SESSION_SIGNING_KEY':'s'*64,
            'PUSHOK_MINIAPP_URL':'https://ads.example.org/miniapp','PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0','BOT_INTERNAL_SECRET':'e'*64})
        self.env.start();self.addCleanup(self.env.stop)
        with topups.connect(self.dsn) as conn:self.username=conn.execute('SELECT telegram_url FROM sponsor_channels WHERE id=%s',(self.channel,)).fetchone()['telegram_url'].rsplit('/',1)[-1]
        self.invite='https://t.me/+'+uuid4().hex
        responses=[{'type':'channel','username':self.username,'id':-100123},
                   {'status':'administrator','can_invite_users':True},{'invite_link':self.invite}]
        with patch.object(analytics.channel_logos,'_call',side_effect=responses) as call:
            r=self.web.post(self.path+'/enable');self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(call.call_args_list[-1].args[1],'createChatInviteLink')
        return self.web.get(self.path).json()

    def action(self,web=None):
        web=web or self.web;data=self.web.get(self.path).json();token=data['url'].rsplit('/',1)[-1]
        landing=web.get('/r/'+token);self.assertEqual(landing.status_code,200)
        self.assertIn('frame-ancestors',landing.headers['content-security-policy'])
        self.assertIn('HttpOnly',landing.headers['set-cookie']);self.assertIn('Secure',landing.headers['set-cookie'])
        action=html.unescape(re.search('action="([^"]+)"',landing.text)[1])
        return action

    def joined(self,**changes):
        payload=dict(chat_id=-100123,telegram_user_id=999,invite_link=self.invite,date=int(time.time()))|changes
        return self.web.post('/api/v1/internal/bot/analytics/join',json=payload,headers={'X-Pushok-Bot-Secret':'e'*64,'X-Pushok-Telegram-Environment':'production'})

    def test_complete_path_duplicates_daily_stats_and_no_personal_data(self):
        self.start()
        # ID подписчика может случайно совпасть с частью даты или публичного токена.
        # Фиксируем такое совпадение: проверка приватности должна учитывать поля JSON.
        with topups.connect(self.dsn) as conn:
            conn.execute("""UPDATE campaign_analytics
                SET enabled_at=date_trunc('second',enabled_at)+interval '0.386999 seconds',token=%s
                WHERE campaign_id=%s""",('999'+'a'*29,self.cid))
        action=self.action()
        for _ in range(2):self.assertEqual(self.web.post(action+'&event=view').status_code,204)
        self.assertEqual(self.web.get(self.path).json()['link_views'],1)
        for _ in range(2):
            r=self.web.post(action,follow_redirects=False);self.assertEqual(r.status_code,303);self.assertEqual(r.headers['location'],self.invite)
        for _ in range(2):self.assertEqual(self.joined().json()['status'],'recorded')
        data=self.web.get(self.path).json();self.assertEqual((data['link_views'],data['clicks'],data['joins'],data['conversion_percent']),(1,1,1,100.0))
        self.assertIsNone(data['reach']);self.assertEqual(int(data['days'][0]['joins']),1)
        # Только агрегаты и публичные параметры ссылки, без полей отдельного подписчика.
        self.assertEqual(set(data),{'enabled','configured','reach','link_views','clicks','joins',
            'conversion_percent','days','enabled_at','test_environment','url','source','timezone','channel_url_snapshot'})
        for day in data['days']:
            self.assertEqual(set(day),{'day','views','clicks','joins'})
        self.assertIn('.386999',data['enabled_at'])
        self.assertEqual(data['url'],'https://ads.example.org/r/'+'999'+'a'*29)
        with patch.dict(os.environ,{'SESSION_SIGNING_KEY':'r'*64}):self.joined()
        self.assertEqual(self.web.get(self.path).json()['joins'],1)
        # Второй подписчик мог открыть пересланное приглашение напрямую: процент не выдумываем.
        self.joined(telegram_user_id=1000);self.assertIsNone(self.web.get(self.path).json()['conversion_percent'])
        with topups.connect(self.dsn) as conn:
            row=conn.execute('SELECT * FROM campaign_link_joins WHERE campaign_id=%s',(self.cid,)).fetchone()
            self.assertEqual(len(row['user_hash']),64);self.assertNotIn('telegram_user_id',row)

    def test_roles_enable_once_tamper_cookie_nonce_and_archive(self):
        self.start()
        with patch.object(analytics.channel_logos,'_call') as call:
            self.assertEqual(self.web.post(self.path+'/enable').status_code,200);call.assert_not_called()
        self.actor={'id':self.other,'role':'advertiser'}
        self.assertEqual(self.web.get(self.path).status_code,404);self.assertEqual(self.web.post(self.path+'/enable').status_code,404)
        self.actor={'id':self.admin,'role':'admin'};self.assertEqual(self.web.get(self.path).status_code,200)
        action=self.action();self.assertEqual(self.web.post(action+'bad').status_code,403)
        with TestClient(fixture.main.app,base_url='https://testserver') as other:
            self.assertEqual(other.post(action).status_code,403)
            second=self.action(other);self.assertEqual(other.post(second,follow_redirects=False).status_code,303)
        self.assertEqual(self.web.get(self.path).json()['clicks'],1)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE campaigns SET archived_at=now() WHERE id=%s',(self.cid,))
        self.assertEqual(self.web.post(action,follow_redirects=False).status_code,404)
        data=self.web.get(self.path).json();self.assertIsNone(data['url']);self.assertEqual(data['clicks'],1)

    def test_wrong_chat_environment_old_and_future_join_ignored(self):
        self.start();self.assertEqual(self.joined(chat_id=-100124).json()['status'],'ignored')
        self.assertEqual(self.joined(date=1).json()['status'],'ignored');self.assertEqual(self.joined(date=int(time.time())+600).json()['status'],'ignored')
        with patch.dict(os.environ,{'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'1'}):
            self.assertEqual(self.joined().status_code,409)
        self.assertEqual(self.web.get(self.path).json()['joins'],0)
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'654321:analytics_rotated'}):
            self.assertEqual(self.web.post(self.path+'/enable').status_code,409)
            self.assertIsNone(self.web.get(self.path).json()['url'])

    def test_missing_rights_does_not_enable_or_leak_telegram_error(self):
        self.start()
        with topups.connect(self.dsn) as conn:conn.execute('DELETE FROM campaign_analytics WHERE campaign_id=%s',(self.cid,))
        with patch.object(analytics.channel_logos,'_call',side_effect=OSError('secret-bot-url')):
            r=self.web.post(self.path+'/enable');self.assertEqual(r.status_code,503);self.assertNotIn('secret-bot-url',r.text)
        self.assertFalse(self.web.get(self.path).json()['enabled'])
