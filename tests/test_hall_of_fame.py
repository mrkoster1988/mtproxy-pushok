"""Календарь, реальные платежи, заморозка правил и атомарные награды."""
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import test_postgres as fixture
from app import hall_of_fame as hall


def dt(s): return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class HallCalendar(unittest.TestCase):
    def test_merged_reward_limits_preserve_valid_legacy_prizes(self):
        self.assertEqual(hall.Settings(rewards={'month':{'spend':[120000000]}}).rewards['month']['spend'],[120000000])
        for metric,amount in [('spend',200000001),('invited',100000001)]:
            with self.assertRaises(ValueError):hall.Settings(rewards={'month':{metric:[amount]}})
    def test_moscow_boundaries(self):
        start,end=hall.bounds('month',dt('2030-01-31T22:00:00'))
        self.assertEqual(start.isoformat(),'2030-02-01T00:00:00+03:00')
        self.assertEqual(end.isoformat(),'2030-03-01T00:00:00+03:00')
        with self.assertRaises(ValueError):
            hall.bounds('week',dt('2030-01-06T22:00:00'))
        self.assertEqual(hall.bounds('year',dt('2030-12-31T22:00:00'))[0].year,2031)

    def test_settings_validation(self):
        for data in ({'top_size':0},{'rewards':{'day':{}}},{'rewards':{'month':{'fake':[1]}}},
                     {'rewards':{'week':{'invited':[-1]}}},{'rewards':{'week':{'invited':[1]*11}}}):
            with self.assertRaises(ValueError):hall.Settings(**data)
        self.assertEqual(hall.Settings().top_size,50)


@unittest.skipUnless(fixture._approved_test_postgres(),'Отдельный тестовый PostgreSQL')
class HallIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    def setUp(self):
        self.owner,self.friend,self.other,self.admin,self.channel,self.tariff=[uuid4() for _ in range(6)]
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM hall_results');conn.execute('DELETE FROM hall_periods');conn.execute('DELETE FROM hall_history_cursor')
            conn.execute("UPDATE users SET created_at='2030-01-01T00:00:00+03'")
            conn.execute("DELETE FROM growth_referrals WHERE created_at>='2030-01-01'")
            conn.execute("UPDATE hall_settings SET started_at='2030-01-01T00:00:00+03',rewards_started_at='2030-01-01T00:00:00+03',top_size=50,rewards=%s",(Jsonb({'month':{'invited':[10000,5000],'spend_rub':[2000]}}),))
            for uid,role in ((self.owner,'advertiser'),(self.friend,'advertiser'),(self.other,'advertiser'),(self.admin,'admin')):
                conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name,created_at) VALUES(%s,%s,%s,'Участник','2030-01-01T00:00:00+03')",(uid,int(uid.hex[:12],16),role))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Test','https://t.me/hall_test')",(self.channel,self.friend))
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'Test',3600,10000)",(self.tariff,))
            for uid in (self.friend,self.other):
                conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,created_at) VALUES(%s,%s,10,90,'2030-01-02')",(uid,self.owner))
        self.actor={'id':self.owner,'role':'advertiser'}
        fixture.main.app.dependency_overrides[fixture.main.get_current_user]=lambda:self.actor
        self.addCleanup(fixture.main.app.dependency_overrides.clear)

    def payment(self,conn,user=None,provider='sbp',test=False,refunded=False,wallet=False,bonus=0,at='2030-01-05',amount=10000):
        cid=uuid4();uid=user or self.friend
        conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
            duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status)
            VALUES(%s,%s,%s,%s,'Test',3600,10000,'RUB','approved',%s)""",(cid,uid,self.channel,self.tariff,'refunded' if refunded else 'verified'))
        currency='XTR' if provider=='telegram_stars' else 'RUB'
        if wallet:
            conn.execute("INSERT INTO wallet_payments(campaign_id,user_id,currency,cash_amount,bonus_amount,created_at) VALUES(%s,%s,'RUB',%s,%s,%s)",(cid,uid,amount,bonus,at))
        else:
            conn.execute('''INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,provider_payment_id,settled_at,telegram_test_environment,sbp_test_mode)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(cid,provider,'telegram_bot' if currency=='XTR' else 'external_web',amount,currency,'refunded' if refunded else 'succeeded',str(uuid4()),at,test,test))
        return cid

    def test_payment_metrics_exclude_test_bonus_refunds_and_staff(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.payment(conn);self.payment(conn,provider='telegram_stars',amount=15)
            self.payment(conn,test=True);self.payment(conn,refunded=True)
            self.payment(conn,wallet=True,amount=500,bonus=9500)
            self.payment(conn,wallet=True,amount=0,bonus=10000)
            self.payment(conn,user=self.admin,amount=99999)
            a,b=hall.bounds('month',dt('2030-01-05'))
            for metric,value in [('spend_rub',10500),('spend_xtr',15),('paid_campaigns',3),('paid_referrals',1),('invited',2)]:
                rows=hall.scores(conn,metric,a,b,50)
                # Administrators now participate; only the installation owner is excluded.
                if metric=='spend_rub':self.assertIn(self.admin,[r['user_id'] for r in rows])
                uid=self.owner if metric in ('invited','paid_referrals') else self.friend
                own=[r for r in rows if r['user_id']==uid]
                self.assertEqual(len(own),1,metric);self.assertEqual(own[0]['score'],value,metric)
            # Второй платёж в следующем месяце не превращает старого реферала в нового.
            self.payment(conn,at='2030-02-06')
            a,b=hall.bounds('month',dt('2030-02-05'));self.assertEqual(hall.scores(conn,'paid_referrals',a,b,50),[])

    def test_flow_gift_freezes_threshold_and_notifies_once(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            # The fixture has exactly two qualifying invitations for the winner.
            rules={'enabled':True,'minimum':2,'days':[90,30,30]}
            conn.execute("UPDATE hall_settings SET flow_gifts=%s,flow_gifts_since='2029-12-01'",(Jsonb(rules),))
            hall.settle(conn,dt('2030-01-10'))
            rules['minimum']=3
            conn.execute("UPDATE hall_settings SET flow_gifts=%s",(Jsonb(rules),))
            hall.settle(conn,dt('2030-02-10'))
            hall.settle(conn,dt('2030-02-10'))
            awards=conn.execute("SELECT user_id,days FROM hall_flow_gifts").fetchall()
            self.assertEqual(awards,[{'user_id':self.owner,'days':90}])
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM vpn_scheduled_notices WHERE kind LIKE 'hall_gift_%'").fetchone()['n'],1)
            conn.rollback()

    def test_frozen_rewards_concurrent_settlement_and_medals(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            hall.settle(conn,dt('2030-01-15'))
            conn.execute('UPDATE hall_settings SET rewards=%s',(Jsonb({'month':{'invited':[99999]}}),))
        def close():
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:return hall.settle(conn,dt('2030-02-02'))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:close(),range(2)))
        close()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            rows=conn.execute("SELECT * FROM wallet_entries WHERE user_id=%s AND operation_key LIKE 'hall:%%'",(self.owner,)).fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['delta'],10000)
            medals=hall.medal_history(conn,self.owner);self.assertEqual(len(medals),1);self.assertEqual(medals[0]['rank'],1)
            period=conn.execute("SELECT * FROM hall_periods WHERE kind='month' AND closed_at IS NOT NULL").fetchone()
            future=conn.execute("SELECT * FROM hall_periods WHERE kind='month' AND closed_at IS NULL").fetchone()
            self.assertEqual(future['settings']['rewards']['month']['invited'],[99999])
        data=self.request('GET',f"/hall?kind=month&period_id={period['id']}")
        self.assertTrue(data['closed']);self.assertEqual(data['rows'][0]['score'],2)
        self.assertEqual(self.request('GET',f'/users/{self.owner}/profile')['frame_rank'],1)

    def test_home_leaders_is_top_ten_independent_of_board_size(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE hall_settings SET top_size=3')
            for _ in range(11):
                inviter,referred=uuid4(),uuid4()
                for uid in (inviter,referred):
                    conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name,created_at) VALUES(%s,%s,'advertiser','Лидер','2030-01-01T00:00:00+03')",(uid,int(uid.hex[:12],16)))
                conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,created_at) VALUES(%s,%s,10,90,'2030-01-02')",(referred,inviter))
        period=hall.bounds('month',dt('2030-01-10'))
        with patch.object(hall,'bounds',return_value=period):
            rows=self.request('GET','/hall/home-leaders')['rows']
            self.assertEqual(len(rows),10)
            self.assertEqual([row['rank'] for row in rows],list(range(1,11)))
            self.assertEqual(rows[0]['user_id'],str(self.owner))
            self.assertTrue(all(set(row)=={'user_id','rank','first_name','last_name'} for row in rows))
            self.assertEqual(len(self.request('GET','/hall?kind=month&metric=invited')['rows']),3)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT top_size FROM hall_settings WHERE singleton').fetchone()[0],3)

    def test_admin_settings_access_and_budget(self):
        self.request('GET','/hall/admin',403)
        self.request('PUT','/hall/admin/settings',403,json={'top_size':50,'rewards':{}})
        self.actor={'id':self.admin,'role':'admin'}
        self.assertIn('totals',self.request('GET','/hall/admin'))
        self.request('PUT','/hall/admin/settings',422,json={'top_size':201,'rewards':{}})
        self.request('PUT','/hall/admin/settings',422,json={'top_size':25,'rewards':{'week':{'invited':[1000]}}})
        self.request('GET','/hall?kind=week',422)
        self.request('PUT','/hall/admin/settings',200,json={'top_size':25,'rewards':{'month':{'invited':[1000]}}})
        self.assertEqual(self.request('GET','/hall/admin')['settings']['top_size'],25)

    def test_failed_credit_rolls_back_period_and_retry(self):
        with patch.object(hall,'post',side_effect=RuntimeError('temporary')):
            with self.assertRaises(RuntimeError):
                with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                    hall.settle(conn,dt('2030-02-02'))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(conn.execute('SELECT count(*) n FROM hall_results').fetchone()['n'],0)
            hall.settle(conn,dt('2030-02-02'))
            self.assertEqual(conn.execute("SELECT count(*) n FROM wallet_entries WHERE user_id=%s AND operation_key LIKE 'hall:%%'",(self.owner,)).fetchone()['n'],1)

    def test_history_before_installation_backfills_despite_newer_periods(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute("UPDATE hall_settings SET started_at='2030-03-01',rewards_started_at='2030-03-01'")
            # Более поздний период уже существует до пересчёта истории.
            a,b=hall.bounds('month',dt('2030-03-05'))
            conn.execute("INSERT INTO hall_periods(kind,starts_at,ends_at,settings) VALUES('month',%s,%s,%s)",
                (a,b,Jsonb({'top_size':50,'rewards':{'month':{'invited':[99999]}}})))
            self.payment(conn)
            hall.settle(conn,dt('2030-03-05'))
            hall.settle(conn,dt('2030-03-05'))
            months=conn.execute("SELECT * FROM hall_periods WHERE kind='month' AND closed_at IS NOT NULL ORDER BY starts_at").fetchall()
            self.assertEqual(len(months),2)
            result=conn.execute("SELECT * FROM hall_results WHERE period_id=%s AND metric='invited'",(months[0]['id'],)).fetchone()
            self.assertEqual(result['user_id'],self.owner);self.assertEqual(result['score'],2)
            self.assertEqual(result['bonus_amount'],0);self.assertIsNone(result['entry_id'])
            self.assertIn('invited',[m['metric'] for m in hall.medal_history(conn,self.owner)])
            self.assertEqual(conn.execute("SELECT count(*) n FROM wallet_entries WHERE user_id=%s AND operation_key LIKE 'hall:%%'",(self.owner,)).fetchone()['n'],0)
            self.assertEqual(hall.counting_start(conn,{'started_at':dt('2030-03-01')}),hall.bounds('month',dt('2030-01-05'))[0])

    def test_owner_excluded_admin_participates_unified_spend_and_own_rank(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s',(self.admin,))
            self.payment(conn,user=self.admin,amount=99999)
            conn.execute("UPDATE users SET role='admin' WHERE id=%s",(self.friend,))
            self.payment(conn,user=self.friend,amount=500)
            self.payment(conn,user=self.friend,provider='telegram_stars',amount=15)
            self.payment(conn,user=self.other,amount=20000)
            a,b=hall.bounds('month',dt('2030-01-05'))
            rows=hall.scores(conn,'spend',a,b,1,self.friend)
            self.assertNotIn(self.admin,[r['user_id'] for r in rows])
            own=next(r for r in rows if r['user_id']==self.friend)
            self.assertEqual(own['score'],10500);self.assertGreater(own['rank'],1)

    def test_secret_endpoints_require_owner_and_owner_cannot_be_demoted(self):
        self.actor={'id':self.admin,'role':'admin','is_owner':False}
        self.request('GET','/admin/sbp/connection',403)
        self.request('PUT','/admin/channel-ai',403,json={'enabled':False,'model':'gpt-4.1-mini','revision':'environment'})
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s',(self.admin,))
            conn.execute("UPDATE users SET role='admin' WHERE id=%s",(self.friend,))
            tg=conn.execute('SELECT telegram_user_id FROM users WHERE id=%s',(self.admin,)).fetchone()[0]
        self.actor={'id':self.friend,'role':'admin','is_owner':False}
        self.request('PUT','/admin/team',403,json={'telegram_user_id':tg,'role':'advertiser'})
        self.request('PUT','/admin/team/access',403,json={'telegram_user_id':tg,'blocked':True})

    def test_legacy_archive_uses_only_frozen_results(self):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            cid=self.payment(conn,user=self.friend)
            hall.settle(conn,dt('2030-02-02'))
            period=conn.execute("SELECT id FROM hall_periods WHERE kind='month' AND starts_at='2030-01-01T00:00:00+03'").fetchone()['id']
            conn.execute("DELETE FROM hall_results WHERE period_id=%s AND metric='spend'",(period,))
        url=f'/hall?kind=month&metric=spend&period_id={period}'
        self.request('GET',url,404)
        self.actor={'id':self.admin,'role':'admin'}
        before=self.request('GET',url)
        self.assertEqual(before['rows'],[])
        self.assertEqual(before['legacy_spend']['spend_rub'][0]['score'],10000)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET payment_status='refunded' WHERE id=%s",(cid,))
            conn.execute('UPDATE users SET blocked_at=now() WHERE id=%s',(self.friend,))
        with patch.object(hall,'scores',side_effect=AssertionError('Archive must not read live scores')):
            after=self.request('GET',url)
        self.assertEqual(after['legacy_spend'],before['legacy_spend'])
        self.assertEqual(after['rows'],[])

    def test_owner_migration_recovers_blocked_initial_choice_once(self):
        sql=(fixture.ROOT/'database/062_owner_migration_recovery.sql').read_text()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
            conn.execute("UPDATE users SET is_owner=true,blocked_at=now(),created_at='2000-01-01' WHERE id=%s",(self.admin,))
            conn.execute("UPDATE users SET role='admin',created_at='2001-01-01' WHERE id=%s",(self.friend,))
            conn.execute(sql)
            self.assertTrue(conn.execute('SELECT is_owner FROM users WHERE id=%s',(self.friend,)).fetchone()['is_owner'])
            self.assertIsNotNone(conn.execute('SELECT blocked_at FROM users WHERE id=%s',(self.admin,)).fetchone()['blocked_at'])
            conn.execute(sql)
            self.assertEqual(conn.execute("SELECT count(*) n FROM audit_events WHERE actor_user_id=%s AND event_type='owner_migration_recovered'",(self.friend,)).fetchone()['n'],1)
            conn.execute("UPDATE users SET role='admin',created_at='1999-01-01' WHERE id=%s",(self.other,))
            conn.execute(sql)
            self.assertTrue(conn.execute('SELECT is_owner FROM users WHERE id=%s',(self.friend,)).fetchone()['is_owner'])
        # docker-entrypoint выполняет SQL без внешнего BEGIN; установщик — в транзакции.
        with psycopg.connect(self.dsn,autocommit=True) as conn:
            prefix,block=sql.split('DO $$',1)
            if any(line.strip() and not line.lstrip().startswith('--') for line in prefix.splitlines()):
                conn.execute(prefix)
            conn.execute('DO $$'+block)
