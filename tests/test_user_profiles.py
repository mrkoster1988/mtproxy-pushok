"""Доступ к профилям, разделение пользователей и безопасная загрузка фото."""
import io
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from PIL import Image
import test_postgres as fixture
from app import channel_logos as photos

main = fixture.main


class UserPhotos(unittest.TestCase):
    def setUp(self):
        photos._cache.clear()
        env = patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'12345:test_secret', 'PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0'})
        env.start();self.addCleanup(env.stop)

    def test_small_photo_reencoded_and_cached_by_user(self):
        output=io.BytesIO();Image.new('RGB',(320,320),'teal').save(output,format='PNG')
        profile={'total_count':1,'photos':[[{'width':160,'height':160,'file_id':'small'}, {'width':640,'height':640,'file_id':'large'}]]}
        with patch.object(photos,'_call',side_effect=[profile,{'file_path':'photos/user.png'}]) as call,patch.object(photos,'_read',return_value=output.getvalue()):
            result=photos.user_avatar(123)
            self.assertEqual(result,photos.user_avatar(123))
            self.assertEqual(call.call_count,2)
            self.assertEqual(call.call_args_list[0].args[1:],('getUserProfilePhotos',{'user_id':123,'offset':0,'limit':1}))
            self.assertEqual(call.call_args_list[1].args[2],{'file_id':'small'})
            with Image.open(io.BytesIO(result)) as image:
                self.assertEqual((image.format,image.size),('JPEG',(160,160)))
        with patch.object(photos,'_download_user',return_value=None) as get:
            photos.user_avatar(124);self.assertEqual(get.call_count,1)

    def test_missing_private_malformed_photo_and_file_path(self):
        for response in ({'photos':[]},{'photos':[None]},{'photos':[[{'file_id':'x','width':'bad','height':160}]]}):
            with patch.object(photos,'_call',return_value=response),patch.object(photos,'_read') as read:
                self.assertIsNone(photos._download_user('12345:test_secret',123));read.assert_not_called()
        profile={'photos':[[{'width':160,'height':160,'file_id':'small'}]]}
        with patch.object(photos,'_call',side_effect=[profile,{'file_path':'https://evil.test/token.jpg'}]),patch.object(photos,'_read') as read:
            self.assertIsNone(photos._download_user('12345:test_secret',123));read.assert_not_called()
        with patch.object(photos,'_download_user',side_effect=OSError('token in URL')) as get:
            self.assertIsNone(photos.user_avatar(500));self.assertIsNone(photos.user_avatar(500));self.assertEqual(get.call_count,1)


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class ProfileIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.owner,self.other,self.staff,self.tariff=[uuid4() for _ in range(4)]
        with psycopg.connect(self.dsn) as conn:
            for uid,role in ((self.owner,'advertiser'),(self.other,'advertiser'),(self.staff,'operator')):
                conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name,last_name,username) VALUES(%s,%s,%s,'Мария','Тестовая','test_user')",(uid,int(uid.hex[:12],16),role))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'День',86400,30000,150)",(self.tariff,))
            self.channels=[]
            for _ in range(12):
                cid=uuid4();self.channels.append(cid)
                conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал',%s)",(cid,self.owner,'https://t.me/channel_'+cid.hex))
            self.campaigns=[]
            for i in range(23):
                cid=uuid4();self.campaigns.append(cid)
                conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                    duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,
                    placement_status,archived_at) VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB','pending',%s,%s)""",
                    (cid,self.owner,self.channels[i%12],self.tariff,'completed' if i<3 else 'cancelled' if i==3 else 'not_scheduled','2026-01-01T00:00:00Z' if i==4 else None))
        self.actor={'id':self.owner,'role':'advertiser'}
        main.app.dependency_overrides[main.get_current_user]=lambda:self.actor
        self.addCleanup(main.app.dependency_overrides.clear)

    def test_own_profile_pagination_archive_and_isolation(self):
        path=f'/users/{self.owner}/profile'
        first=self.request('GET',path)
        self.assertEqual(first['counts'],{'total':23,'current':18,'archived':5,'active':0,'channels':12,'invited':0})
        self.assertTrue(first['more_channels']);self.assertTrue(first['more_campaigns'])
        second=self.request('GET',path+'?campaign_page=1&channel_page=1')
        self.assertEqual(len(second['campaigns']),8);self.assertEqual(len(second['channels']),2)
        self.assertFalse({x['id'] for x in first['campaigns']} & {x['id'] for x in second['campaigns']})
        self.assertFalse(second['more_campaigns']);self.assertFalse(second['more_channels'])
        archive=self.request('GET',path+'?archived=true');self.assertEqual(len(archive['campaigns']),5)
        self.assertEqual(first['campaigns'][0]['stars_price_snapshot'],150)
        self.request('GET',path+'?campaign_page=-1',422)
        self.actor={'id':self.other,'role':'advertiser'}
        self.request('GET',path,404)
        self.assertEqual(self.request('GET',f'/users/{self.other}/profile')['counts']['total'],0)
        self.actor={'id':self.staff,'role':'operator'}
        self.assertEqual(self.request('GET',path)['user']['id'],str(self.owner))
        self.request('GET',f'/users/{uuid4()}/profile',404)

    def test_referral_count_belongs_to_viewed_user(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days) VALUES(%s,%s,10,90)', (self.other,self.owner))
        path=f'/users/{self.owner}/profile'
        self.assertEqual(self.request('GET',path)['counts']['invited'],1)
        self.actor={'id':self.staff,'role':'operator'}
        self.assertEqual(self.request('GET',path)['counts']['invited'],1)
        self.assertEqual(self.request('GET',f'/users/{self.staff}/profile')['counts']['invited'],0)

    def test_avatar_authorization_happens_before_download(self):
        path=f'/api/v1/users/{self.owner}/avatar'
        with patch('app.user_profiles.user_avatar',return_value=b'jpeg') as download:
            self.actor={'id':self.other,'role':'advertiser'}
            self.assertEqual(self.client.get(path).status_code,404);download.assert_not_called()
            self.actor={'id':self.staff,'role':'operator'}
            result=self.client.get(path)
            self.assertEqual(result.status_code,200);self.assertEqual(result.headers['cache-control'],'private, no-store')
            self.assertTrue(result.headers['content-type'].startswith('image/jpeg'))
            download.return_value=None
            self.actor={'id':self.owner,'role':'advertiser'}
            self.assertEqual(self.client.get(path).status_code,204)
        main.app.dependency_overrides.clear()
        self.assertEqual(self.client.get(path).status_code,401)

    def test_staff_campaign_response_links_only_authorized_profiles(self):
        self.actor={'id':self.staff,'role':'operator'}
        rows=self.request('GET','/operator/campaigns')
        mine=[c for c in rows if c['id'] in {str(cid) for cid in self.campaigns}]
        self.assertTrue(mine)
        self.assertTrue(all(c['advertiser_user_id']==str(self.owner) for c in mine))
        self.actor={'id':self.owner,'role':'advertiser'}
        self.assertTrue(all('advertiser_user_id' not in c for c in self.request('GET','/campaigns')))
