import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.responses import JSONResponse

import test_postgres as fixture


@unittest.skipUnless(fixture._approved_test_postgres(), 'Отдельный PostgreSQL')
class ContestGifts(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request

    def test_competition_lifecycle(self):
        def user(role):
            uid = uuid4()
            return dict(id=uid, role=role, telegram_user_id=int(uid.hex[:12],16))
        admin, organizer, winner, stranger = [user(role) for role in ('admin','advertiser','advertiser','advertiser')]
        with psycopg.connect(self.dsn) as conn:
            for item in (admin, organizer, winner, stranger):
                conn.execute('INSERT INTO users(id,role,telegram_user_id) VALUES(%(id)s,%(role)s,%(telegram_user_id)s)',item)
        who = fixture.main.get_current_user
        overrides = fixture.main.app.dependency_overrides
        self.addCleanup(overrides.clear)
        overrides[who] = lambda: admin
        base = '/contests'
        event = self.request('POST',base,json=dict(title='Конкурс',sender='Канал',organizer=organizer['telegram_user_id'],month_limit=1,quarter_limit=0))
        path = base+'/events/'+event['id']
        overrides[who] = lambda: stranger
        self.request('GET',path,404)
        overrides[who] = lambda: organizer
        body = dict(days=30,request_id=str(uuid4()),recipient=winner['telegram_user_id'])
        prize = self.request('POST',path+'/prizes',json=body)
        self.assertEqual(self.request('POST',path+'/prizes',json=body)['id'],prize['id'])
        self.request('POST',path+'/prizes',409,json=dict(days=30,request_id=str(uuid4())))
        self.request('POST',path+'/prizes/'+prize['id']+'/revoke')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.client.post('/api/v1'+path+'/prizes',json=dict(days=30,request_id=str(uuid4()))),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        opened=next(r.json() for r in results if r.status_code==200)
        claim=base+'/prizes/'+opened['id']+'/claim'
        overrides[who] = lambda: stranger
        self.request('POST',base+'/prizes/'+prize['id']+'/claim',404)
        overrides[who] = lambda: winner
        self.request('POST',base+'/prizes/'+prize['id']+'/claim',410)
        with patch('app.contest_gifts.vpn_shop.exchange',side_effect=[JSONResponse({'expires':None}),JSONResponse({'expires':1900000000})]) as exchange:
            self.assertIsNone(self.request('POST',claim)['expires'])
            overrides[who] = lambda: stranger
            self.request('POST',claim,410)
            overrides[who] = lambda: winner
            for _ in range(2): self.assertEqual(self.request('POST',claim)['expires'],1900000000)
            self.assertEqual(exchange.call_count,2)
        self.assertEqual(self.request('GET',base+'/mine')[0]['id'],opened['id'])
