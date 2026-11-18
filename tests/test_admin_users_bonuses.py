"""Поиск, приватность баланса и редактирование бонусов с сохранением проводок."""
import unittest
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import wallet_ledger as ledger

main=fixture.main

@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class AdminUsersBonuses(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.admin,self.owner,self.other,self.operator,self.tariff=[uuid4() for _ in range(5)]
        self.tg=int(self.owner.hex[:12],16)
        self.handle='search_'+self.owner.hex[:12]
        with psycopg.connect(self.dsn) as conn:
            for i,(uid,role) in enumerate([(self.admin,'admin'),(self.owner,'advertiser'),(self.other,'advertiser'),(self.operator,'operator')]):
                conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,%s,%s)',(uid,self.tg+i,role,'Поиск '+str(i),self.handle if uid==self.owner else None))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Тариф',3600,10000,151)",(self.tariff,))
        self.actor={'id':self.admin,'role':'admin'}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)

    def test_directory_category_tracks_actual_launch(self):
        def matches(category):
            return self.request('GET','/admin/users',params={'category':category,'query':self.handle})['total']
        self.assertEqual(matches('user'),1)
        self.assertEqual(matches('advertiser'),0)
        channel, campaign, node = uuid4(), uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Тест',%s,443)",(node,node.hex+'.example.org'))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал','https://t.me/test_launch')",(channel,self.owner))
            conn.execute("""INSERT INTO campaigns(id,channel_id,tariff_id,advertiser_user_id,
                tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,
                placement_status,proxy_node_id,scheduled_start_at,scheduled_end_at)
                VALUES(%s,%s,%s,%s,'Тест',3600,0,'RUB','scheduled',%s,now(),now()+interval '1 hour')""",
                (campaign,channel,self.tariff,self.owner,node))
        self.assertEqual(matches('user'),1)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='active' WHERE id=%s",(campaign,))
            conn.execute("UPDATE campaigns SET placement_status='cancelled',archived_at=now() WHERE id=%s",(campaign,))
        self.assertEqual(matches('user'),0)
        self.assertEqual(matches('advertiser'),1)
        self.assertTrue(self.request('GET',f'/users/{self.owner}/profile')['user']['has_launched_ads'])

    def test_developer_text_permissions_and_concurrent_edits(self):
        path='/community/developer'
        initial=self.request('GET',path)
        change=dict(description='Кратко о разработчике',history='История кабинета',revision=initial['revision'])
        self.request('PUT',path,403,json=change)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s',(self.admin,))
        try:
            saved=self.request('PUT',path,json=change)
            self.assertEqual(saved['history'],change['history'])
            self.request('PUT',path,409,json=change)
            self.request('PUT',path,422,json={**change,'description':'  '})
            self.actor={'id':self.other,'role':'advertiser'}
            self.assertEqual(self.request('GET',path),saved)
            self.request('PUT',path,403,json={**change,'revision':saved['revision']})
        finally:
            with psycopg.connect(self.dsn) as conn:
                conn.execute('UPDATE users SET is_owner=false WHERE id=%s',(self.admin,))

    def test_directory_sort_applies_before_pagination(self):
        marker='ordering_'+self.owner.hex[:12]
        with psycopg.connect(self.dsn) as conn:
            for i in range(23):
                conn.execute('''INSERT INTO users(telegram_user_id,first_name,created_at)
                    VALUES(%s,%s,now()+(%s * interval '1 minute'))''',
                    (self.tg+100+i,f'{marker} {22-i:02}',i))
        def names(sort,page):
            result=self.request('GET','/admin/users',params={'query':marker,'sort':sort,'page':page})
            return [row['first_name'] for row in result['items']]
        ascending=[f'{marker} {i:02}' for i in range(23)]
        self.assertEqual(names('name',0)+names('name',1),ascending)
        self.assertEqual(names('newest',0)+names('newest',1),ascending)
        self.assertEqual(names('oldest',0)+names('oldest',1),ascending[::-1])
        self.request('GET','/admin/users',422,params={'sort':'unknown'})

    def test_directory_username_roles_visits_and_balance_privacy(self):
        row=self.request('GET','/admin/users',params={'query':'@'+self.handle.upper()})
        self.assertEqual([x['id'] for x in row['items']],[str(self.owner)])
        self.assertEqual(self.request('GET','/admin/users',params={'query':'%\' OR true--'})['total'],0)
        self.assertEqual(self.request('GET','/admin/users',params={'query':str(self.owner)})['total'],1)
        self.assertEqual(self.request('GET','/admin/users',params={'query':self.handle,'visited':True})['total'],0)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            ledger.post(conn,self.owner,'BONUS',12345,'manual_credit','test:'+str(uuid4()),'Тест',actor=self.admin)
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(uuid4(),self.owner))
            conn.execute('DELETE FROM user_sessions WHERE user_id=%s',(self.owner,))
        self.assertEqual(self.request('GET','/admin/users',params={'query':self.handle,'visited':True})['total'],1)
        self.assertEqual(self.request('GET',f'/users/{self.owner}/profile')['wallet'],{'RUB':0,'BONUS':12345,'XTR':0})
        for uid,role in [(self.operator,'operator'),(self.owner,'advertiser')]:
            self.actor={'id':uid,'role':role}
            self.request('GET','/admin/users',200 if role=='operator' else 403)
            self.assertNotIn('wallet',self.request('GET',f'/users/{self.owner}/profile'))

    def test_gift_edit_delete_preserves_credit_and_blocks_new_activation(self):
        body=dict(request_id=str(uuid4()),name='Подарок',amount=1200,max_uses=2,expires_at=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
        gift=self.request('POST','/admin/wallet/gifts',201,json=body);path='/admin/wallet/gifts/'+gift['id']
        edit={k:v for k,v in body.items() if k!='request_id'}
        self.request('PATCH',path,json={**edit,'amount':1500})
        self.actor={'id':self.owner,'role':'advertiser'}
        first=self.request('POST','/wallet/gifts/redeem',json={'code':gift['code']})
        self.assertEqual(first['amount'],1500)
        self.actor={'id':self.admin,'role':'admin'}
        self.request('PATCH',path,409,json=edit)
        self.request('PATCH',path,json={**edit,'amount':1500,'name':'Подарок изменён','max_uses':3})
        self.request('DELETE',path)
        self.assertNotIn(gift['id'],[g['id'] for g in self.request('GET','/admin/wallet/gifts')['items']])
        self.assertIn(gift['id'],[g['id'] for g in self.request('GET','/admin/wallet/gifts?deleted=true')['items']])
        self.assertEqual(self.request('GET',path+'/redemptions')['credited'],1500)
        self.request('PUT',path,404,json={'active':True})
        self.actor={'id':self.other,'role':'advertiser'}
        self.request('POST','/wallet/gifts/redeem',409,json={'code':gift['code']})
        self.actor={'id':self.owner,'role':'advertiser'}
        retry=self.request('POST','/wallet/gifts/redeem',json={'code':gift['code']})
        self.assertTrue(retry['already_redeemed']);self.assertEqual(retry['amount'],1500)
        self.actor={'id':self.admin,'role':'admin'}
        self.request('POST',path+'/restore')
        self.assertFalse(next(g for g in self.request('GET','/admin/wallet/gifts')['items'] if g['id']==gift['id'])['active'])

    def test_offer_edit_keeps_existing_price_and_personal_recipient(self):
        body=dict(kind='promo',name='Персональная скидка',code='TEST'+uuid4().hex[:12],percent=20,tariff_id=str(self.tariff),recipient_user_id=str(self.owner),total_limit=3)
        offer=self.request('POST','/admin/growth/offers',201,json=body);path='/admin/growth/offers/'+offer['id']
        self.actor={'id':self.other,'role':'advertiser'}
        self.request('GET','/growth/shared/'+offer['id'],409)
        self.actor={'id':self.owner,'role':'advertiser'}
        self.assertEqual(self.request('GET','/growth/shared/'+offer['id'])['quote']['stars_price_xtr'],121)
        cid,channel=uuid4(),uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Тест',%s)",(channel,self.owner,'https://t.me/test_'+channel.hex))
            conn.execute("INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,growth_offer_id) VALUES(%s,%s,%s,%s,'Тариф',3600,8000,'RUB',%s)",(cid,self.owner,channel,self.tariff,offer['id']))
            conn.execute('INSERT INTO growth_redemptions(campaign_id,offer_id,user_id) VALUES(%s,%s,%s)',(cid,offer['id'],self.owner))
        self.actor={'id':self.admin,'role':'admin'}
        self.request('PUT',path,409,json={**body,'recipient_user_id':str(self.other)})
        self.request('PUT',path,json={**body,'percent':30,'per_user_limit':2,'name':'Новая скидка'})
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT price_kopecks_snapshot FROM campaigns WHERE id=%s',(cid,)).fetchone()[0],8000)
        self.request('DELETE',path)
        self.assertTrue(any(o['id']==offer['id'] for o in self.request('GET','/admin/growth?deleted=true')['offers']))
        self.actor={'id':self.owner,'role':'advertiser'}
        self.request('GET','/growth/shared/'+offer['id'],409)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE offer_id=%s',(offer['id'],)).fetchone()[0],1)
