"""Персональные коды и журнал выдачи: получатель, права, CSV и миграции."""
import csv
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import wallet_gifts as gifts, wallet_topups as topups


class PersonalGiftValidation(unittest.TestCase):
    def test_recipient_requires_numeric_id_and_single_use(self):
        data=dict(request_id=uuid4(),name='Личный подарок',amount=100,max_uses=1,expires_at=datetime.now(timezone.utc)+timedelta(days=1))
        for value in (0,-1,True,'12345',1.5,2**52):
            with self.assertRaises(ValueError):gifts.GiftCreate(**data,recipient_telegram_id=value)
        with self.assertRaises(ValueError):gifts.GiftCreate(**{**data,'max_uses':2},recipient_telegram_id=12345)
        self.assertIsNone(gifts.GiftCreate(**data).recipient_telegram_id)
        self.assertEqual(gifts.GiftCreate(**data,recipient_telegram_id=12345).recipient_telegram_id,12345)


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class PersonalGiftsIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    balances=wallet_fixture.WalletIntegration.balances

    def setUp(self):
        wallet_fixture.WalletIntegration.setUp(self)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=true')

    def payload(self,**kw):
        return gifts.GiftCreate(request_id=uuid4(),name='Подарок клиенту',amount=12345,
                               expires_at=datetime.now(timezone.utc)+timedelta(days=1),**{'max_uses':1,**kw})

    def create(self,data=None):
        with topups.connect(self.dsn) as conn:return gifts.create(conn,self.admin,data or self.payload())

    def redeem(self,code,uid=None):
        with topups.connect(self.dsn) as conn:return gifts.redeem(conn,uid or self.owner,code)

    def admin_report(self,gid,page=0):
        self.actor={'id':self.admin,'role':'admin'}
        return self.request('GET','/admin/wallet/gifts/'+str(gid)+'/redemptions?page='+str(page))

    def test_personal_gift_wrong_user_cannot_consume_or_read_recipient(self):
        gift=self.create(self.payload(recipient_telegram_id=self.tg))
        self.actor={'id':self.other,'role':'advertiser'}
        denied=self.request('POST','/wallet/gifts/redeem',409,json={'code':gift['code']})
        self.assertEqual(denied,{'detail':'wallet_gift_unavailable'})
        with topups.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM wallet_gift_redemptions WHERE gift_id=%s',(gift['id'],)).fetchone()['n'],0)
            conn.execute('UPDATE users SET username=%s WHERE id=%s',('new_username',self.owner))
        result=self.redeem(gift['code']);self.assertFalse(result['already_redeemed'])
        self.assertTrue(self.redeem(gift['code'])['already_redeemed'])
        self.assertEqual(self.balances()['BONUS'],12345)
        report=self.admin_report(gift['id']);self.assertEqual(report['total'],1);self.assertEqual(report['credited'],12345)
        self.assertEqual(report['items'][0]['telegram_user_id'],self.tg)
        self.assertEqual(report['items'][0]['username'],'new_username')

    def test_personal_code_can_be_created_before_recipient_joins(self):
        tg=self.tg+500;gift=self.create(self.payload(recipient_telegram_id=tg))
        with self.assertRaises(HTTPException):self.redeem(gift['code'])
        uid=uuid4()
        with topups.connect(self.dsn) as conn:conn.execute('INSERT INTO users(id,telegram_user_id) VALUES(%s,%s)',(uid,tg))
        self.assertEqual(self.redeem(gift['code'],uid)['amount'],12345)
        self.assertEqual(self.admin_report(gift['id'])['items'][0]['telegram_user_id'],tg)

    def test_idempotency_includes_recipient(self):
        data=self.payload(recipient_telegram_id=self.tg);first=self.create(data)
        self.assertEqual(self.create(data)['id'],first['id'])
        with self.assertRaises(HTTPException) as err:self.create(data.model_copy(update={'recipient_telegram_id':self.tg+1}))
        self.assertEqual(err.exception.detail,'wallet_request_conflict')
        with self.assertRaises(HTTPException):self.create(data.model_copy(update={'recipient_telegram_id':None}))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.redeem(first['code']),range(2)))
        self.assertEqual(sum(not r['already_redeemed'] for r in results),1)
        self.assertEqual(self.balances()['BONUS'],12345)

    def test_reports_and_export_are_admin_only(self):
        gift=self.create();self.redeem(gift['code'])
        for role,uid in [('advertiser',self.owner),('advertiser',self.other),('operator',self.other)]:
            self.actor={'id':uid,'role':role}
            self.request('GET','/admin/wallet/gifts/'+str(gift['id'])+'/redemptions',403)
            self.request('GET','/admin/wallet/gifts/'+str(gift['id'])+'/export',403)
        self.actor={'id':self.admin,'role':'admin'}
        self.request('GET','/admin/wallet/gifts/'+str(uuid4())+'/redemptions',404)
        self.request('GET','/admin/wallet/gifts/'+str(uuid4())+'/export',404)
        self.request('GET','/admin/wallet/gifts/'+str(gift['id'])+'/redemptions?page=-1',422)

    def test_report_pagination_totals_and_csv_formula_escaping(self):
        data=self.payload(max_uses=35).model_copy(update={'name':'=SUM(1)'});gift=self.create(data)
        ids=[self.owner]
        with topups.connect(self.dsn) as conn:
            conn.execute("UPDATE users SET first_name='=1+1',username='@danger' WHERE id=%s",(self.owner,))
            for n in range(31):
                uid=uuid4();ids.append(uid)
                conn.execute('INSERT INTO users(id,telegram_user_id) VALUES(%s,%s)',(uid,self.tg+100+n))
        for uid in ids:self.redeem(gift['code'],uid)
        first=self.admin_report(gift['id']);second=self.admin_report(gift['id'],1)
        self.assertEqual(first['total'],32);self.assertEqual(first['credited'],32*12345)
        self.assertEqual(len(first['items']),30);self.assertEqual(len(second['items']),2)
        self.assertFalse({r['entry_id'] for r in first['items']} & {r['entry_id'] for r in second['items']})
        response=self.client.get('/api/v1/admin/wallet/gifts/'+str(gift['id'])+'/export')
        self.assertEqual(response.status_code,200,response.text);self.assertIn('no-store',response.headers['Cache-Control'])
        rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),33)
        self.assertTrue(all(r[3].endswith('+00:00') for r in rows[1:]))
        self.assertTrue(all(r[0].startswith("'=") for r in rows[1:]))
        owner=next(r for r in rows[1:] if r[4]==str(self.tg));self.assertEqual(owner[5],"'@danger");self.assertEqual(owner[6],"'=1+1");self.assertEqual(owner[7],'12345')

    def test_reporting_survives_disabling_and_reapplying_new_migration(self):
        gift=self.create(self.payload(recipient_telegram_id=self.tg));self.redeem(gift['code'])
        root=Path(__file__).resolve().parents[1]
        with topups.connect(self.dsn) as conn:
            conn.execute('UPDATE wallet_settings SET enabled=false')
            conn.execute('UPDATE wallet_gifts SET active=false WHERE id=%s',(gift['id'],))
            for _ in range(2):conn.execute((root/'database/039_personal_gifts.sql').read_text())
        report=self.admin_report(gift['id']);self.assertEqual(report['credited'],12345);self.assertEqual(report['total'],1)
        self.assertEqual(report['gift']['recipient_telegram_id'],self.tg)
        with topups.connect(self.dsn) as conn:conn.execute('UPDATE wallet_settings SET enabled=true')
        general=self.create();self.assertIsNone(general['recipient_telegram_id']);self.redeem(general['code'],self.other)
