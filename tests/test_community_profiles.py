"""Права, перенос без финансовых изменений, верификация и фоновая модерация."""
import unittest
from uuid import uuid4
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import automatic_moderation as ai
main=fixture.main

@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен тестовый PostgreSQL')
class CommunityIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    def db(self,sql,args=()):
        with psycopg.connect(self.dsn,row_factory=dict_row) as c:
            q=c.execute(sql,args);return q.fetchall() if q.description else None
    def setUp(self):
        self.ids=[uuid4() for _ in range(4)];self.owner,self.admin,self.member,self.friend=self.ids
        self.db('UPDATE users SET is_owner=false WHERE is_owner')
        for uid,role in zip(self.ids,['admin','admin','advertiser','advertiser']):
            self.db('INSERT INTO users(id,telegram_user_id,role,is_owner,first_name) VALUES(%s,%s,%s,%s,%s)',(uid,int(uid.hex[:12],16),role,uid==self.owner,'Участник'))
        self.actor={'id':self.member,'role':'advertiser','telegram_user_id':int(self.member.hex[:12],16)}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)
    def act(self,uid):
        self.actor={'id':uid,'role':'admin' if uid in (self.owner,self.admin) else 'advertiser','is_owner':uid==self.owner,'telegram_user_id':int(uid.hex[:12],16)}
    def verify(self,uid):
        self.db("INSERT INTO verified_profiles(user_id,phone_number,phone_verified_at) VALUES(%s,'+79991234567',now()) ON CONFLICT(user_id,telegram_environment) DO UPDATE SET phone_verified_at=now()",(uid,))
    def test_presence_owner_hidden_and_public_profile_private(self):
        self.request('POST','/community/presence');data=self.request('GET',f'/community/users/{self.member}')
        self.assertTrue(data['online']);self.assertNotIn('phone_number',data);self.assertNotIn('telegram_user_id',data)
        self.act(self.owner);self.request('POST','/community/presence');self.assertIsNone(self.request('GET',f'/community/users/{self.owner}')['online'])
        self.act(self.friend);self.request('GET',f'/community/users/{self.member}/invited',403)
        self.act(self.member);self.assertEqual(self.request('GET',f'/community/users/{self.member}/invited')['items'],[])
    def test_owner_edit_transfer_locks_source_and_preserves_money(self):
        self.verify(self.member)
        body={'phone_number':'+79997654321','birthday':{'day':17,'month':11,'year':1990}}
        self.act(self.admin);self.request('PUT',f'/community/users/{self.member}/personal',403,json=body)
        self.act(self.owner);self.request('PUT',f'/community/users/{self.member}/personal',json=body)
        self.db("INSERT INTO birthday_rewards(user_id,reward_year,birthday_date,bonus_amount) VALUES(%s,2026,'2026-11-17',10000)",(self.member,))
        from app.wallet_ledger import post
        with psycopg.connect(self.dsn,row_factory=dict_row) as c:post(c,self.member,'BONUS',10000,'gift_credit',str(uuid4()),'Тест')
        transfer={'target_telegram_id':int(self.friend.hex[:12],16)}
        self.request('POST',f'/community/users/{self.member}/personal/transfer',json=transfer)
        self.assertEqual(self.db("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'",(self.member,))[0]['balance'],10000)
        self.assertEqual(self.db('SELECT bonus_amount FROM birthday_rewards WHERE user_id=%s',(self.friend,))[0]['bonus_amount'],0)
        self.act(self.member);self.assertFalse(self.request('GET','/me/verification')['phone_verified']);self.request('PUT','/me/birthday',409,json={'birthday':{'day':1,'month':1},'birthday_opt_in':True})
        self.act(self.friend);self.assertTrue(self.request('GET','/me/verification')['phone_verified']);self.request('PUT','/me/birthday',409,json={'birthday':{'day':1,'month':1},'birthday_opt_in':True})
        self.request('PUT','/me/birthday',json={'birthday':body['birthday'],'birthday_opt_in':False})
        self.act(self.owner);self.request('POST',f'/community/users/{self.friend}/personal/transfer',409,json={'target_telegram_id':int(self.member.hex[:12],16)})
    def campaign(self):
        channel,tariff=uuid4(),uuid4()
        self.db("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Обычный канал',%s)",(channel,self.member,'https://t.me/channel_'+channel.hex[:10]))
        self.db("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'День',86400,30000)",(tariff,))
        return {'channel_id':str(channel),'tariff_id':str(tariff)}
    def test_creation_requires_verified_phone_all_surfaces(self):
        body=self.campaign();self.request('POST','/campaigns',403,json=body)
        order={**body,'request_id':str(uuid4()),'expected_price_kopecks':30000,'expected_duration_seconds':86400}
        self.request('POST','/orders',403,json=order)
        with patch.dict('os.environ',{'DATABASE_URL':self.dsn,'BOT_INTERNAL_SECRET':'f'*64}):
            self.request('POST','/internal/bot/campaign-drafts',403,headers={'X-Pushok-Bot-Secret':'f'*64},json={'telegram_user_id':self.actor['telegram_user_id'],'channel_username':'test_channel','request_key':str(uuid4())})
        self.verify(self.member);self.request('POST','/campaigns',201,json=body)
        self.request('POST','/orders',201,json=order)
    def test_ai_approval_review_and_stale_response(self):
        self.verify(self.member);body=self.campaign();cid=self.request('POST','/campaigns',201,json=body)['id']
        self.db("UPDATE campaigns SET moderation_status='pending' WHERE id=%s",(cid,))
        config=('not-a-real-key','model');posts=['Обучение таблицам и работе с формулами. Примеры задач и решения для начинающих. '*3]*3
        with patch.object(ai.settings,'active',return_value=config),patch.object(ai.settings,'current',return_value={'revision':'test'}),patch.object(ai.topics,'public_posts',return_value=posts),patch.object(ai,'classify',return_value=ai.Decision(decision='approve',reason='Образовательный канал')):
            self.assertEqual(ai.service_once(self.dsn)['approved'],1)
            self.assertEqual(ai.service_once(self.dsn)['approved'],0)
            self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s',(cid,))[0]['moderation_status'],'approved')
            cid2=self.request('POST','/campaigns',201,json=body)['id'];self.db("UPDATE campaigns SET moderation_status='pending' WHERE id=%s",(cid2,))
            with patch.object(ai.topics,'public_posts',return_value=['18+ порно']*3):self.assertEqual(ai.service_once(self.dsn)['approved'],0)
            self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s',(cid2,))[0]['decision'],'review')
            self.db("UPDATE campaigns SET updated_at=clock_timestamp() WHERE id=%s",(cid2,))
            def changed(*args):
                self.db("UPDATE campaigns SET moderation_status='rejected',updated_at=clock_timestamp() WHERE id=%s",(cid2,))
                return ai.Decision(decision='approve',reason='Устаревший ответ')
            with patch.object(ai,'classify',side_effect=changed):self.assertEqual(ai.service_once(self.dsn)['approved'],0)
            self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s',(cid2,))[0]['moderation_status'],'rejected')

    def test_ai_service_profile_counts_actual_decisions_and_access(self):
        self.verify(self.member)
        cid=self.request('POST','/campaigns',201,json=self.campaign())['id']
        self.request('GET','/users/ai-agent/profile',403)
        self.act(self.admin)
        before=self.request('GET','/users/ai-agent/profile')['counts']
        self.db("INSERT INTO ai_moderation_events(campaign_id,decision,reason,revision,topic) VALUES(%s,'approved','Проверено','test','technology'),(%s,'review','Нужен сотрудник','test','adult'),(%s,'retry','Сеть','test',NULL)",(cid,cid,cid))
        data=self.request('GET','/users/ai-agent/profile')
        self.assertEqual(data['account']['id'],'ai-agent')
        self.assertEqual(data['counts']['approved'],before['approved']+1)
        self.assertEqual(data['counts']['review'],before['review']+1)
        self.assertEqual(data['counts']['rejected'],before['rejected'])
        self.assertEqual(data['counts']['retry'],before['retry']+1)
        self.assertFalse(data['automatic_rejection_enabled'])
        self.assertNotIn('telegram_user_id',data['account'])
        self.assertNotIn('wallet',data)
        self.assertTrue(any(t['topic']=='adult' and t['review']>=1 for t in data['topics']))
        self.db("UPDATE campaigns SET moderation_status='approved',topic='entertainment' WHERE id=%s",(cid,))
        self.db("INSERT INTO ai_moderation_checks(campaign_id,snapshot,revision,decision,reason) SELECT id,updated_at,'test','approved','Проверено' FROM campaigns WHERE id=%s",(cid,))
        self.assertTrue(self.request('GET','/community/moderation/'+cid)['check']['approved_by_agent'])
        report=self.request('GET','/campaigns/'+cid+'/report')
        self.assertTrue(any(e['event']=='campaign_approved_by_ai' for e in report['timeline']))
        self.assertNotIn('reason',report['timeline'][0])
        self.db("INSERT INTO audit_events(actor_user_id,campaign_id,event_type) VALUES(%s,%s,'campaign_moderated')",(self.admin,cid))
        self.assertFalse(self.request('GET','/community/moderation/'+cid)['check']['approved_by_agent'])
        self.assertTrue(any(t['topic']=='technology' and t['approved']>=1 for t in self.request('GET','/users/ai-agent/profile')['topics']))
