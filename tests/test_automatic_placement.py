"""Оплата → полный интервал без ручного выбора, с конфликтами и повторными worker."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import auto_schedule, sbp_worker

HOUR = timedelta(hours=1)


class SchedulerWorkerTests(unittest.TestCase):
    def test_scheduler_starts_while_payment_reconciliation_is_blocked(self):
        import os
        stopped, scheduled = threading.Event(), threading.Event()
        def schedule(*args, **kwargs):
            scheduled.set();return {'scheduled': 1}
        def reconciliation(*args, **kwargs):
            self.assertTrue(scheduled.wait(2), 'Bank polling must not delay scheduling')
            stopped.set();return {'status':'disabled'}
        with patch('sys.argv',['worker','--apply']),patch.dict(os.environ,{'DATABASE_URL':'test-only'}), \
             patch.object(sbp_worker,'threading',SimpleNamespace(Event=lambda:stopped,Thread=threading.Thread)), \
             patch.object(sbp_worker.signal,'signal'),patch.object(sbp_worker,'schedule_once',side_effect=schedule), \
             patch.object(sbp_worker,'service_once',side_effect=reconciliation), \
             patch('app.wallet_worker.service_once',return_value={}),patch('builtins.print'):
            sbp_worker.main()
        self.assertTrue(scheduled.is_set())

    def test_database_error_retries_on_next_tick(self):
        stopped=threading.Event()
        with patch.object(sbp_worker,'schedule_once',side_effect=[psycopg.OperationalError(),{'scheduled':0}]) as work, \
             patch.object(stopped,'wait',side_effect=[False,True]),patch.object(stopped,'is_set',side_effect=[False,False,True]), \
             patch('builtins.print'):
            sbp_worker.schedule_loop('test-only',stopped)
        self.assertEqual(work.call_count,2)


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class AutomaticPlacementTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    _management_fixture=fixture.PostgresIntegration._management_fixture

    def setUp(self):
        self.f=self._management_fixture()
        self.start=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(days=2)
        with psycopg.connect(self.dsn) as c:
            c.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment',price_kopecks_snapshot=100,requested_start_at=%s WHERE id=%s",(self.start,self.f['campaign']))
        fixture.main.app.dependency_overrides[fixture.main.get_current_user]=lambda:{'id':self.f['admin'],'role':'admin'}

    def row(self,cid=None):
        with psycopg.connect(self.dsn,row_factory=dict_row) as c:
            return c.execute('SELECT * FROM campaigns WHERE id=%s',(cid or self.f['campaign'],)).fetchone()

    def paid(self,cid=None):
        with psycopg.connect(self.dsn) as c:
            c.execute("UPDATE campaigns SET payment_status='verified' WHERE id=%s",(cid or self.f['campaign'],))

    def other(self,node=None,start=None,duration=HOUR,paid=True):
        cid=uuid4()
        with psycopg.connect(self.dsn) as c:
            c.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,
                placement_status,proxy_node_id,scheduled_start_at,scheduled_end_at,requested_start_at)
                VALUES(%s,%s,%s,%s,'Тест',%s,100,'RUB','approved',%s,%s,%s,%s,%s,%s)""",
                (cid,self.f['owner'],self.f['channel'],self.f['tariff'],int(duration.total_seconds()),
                 'verified' if paid else 'awaiting_payment','scheduled' if node else 'not_scheduled',
                 node,start if node else None,start+duration if node else None,self.start))
        def cleanup():
            with psycopg.connect(self.dsn) as c:c.execute("UPDATE campaigns SET placement_status='cancelled' WHERE id=%s",(cid,))
        self.addCleanup(cleanup);return cid

    def bind(self,node):
        fixture.main.app.dependency_overrides[fixture.main.get_current_user]=lambda:{'id':self.f['admin'],'role':'admin'}
        self.request('PUT','/admin/promotion-bindings',json=dict(node_id=str(node),channel_id=str(self.f['channel']),ad_tag=uuid4().hex,telegram_binding_confirmed=True))

    def test_payment_assigns_requested_time_without_binding_and_is_not_activation(self):
        auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['placement_status'],'not_scheduled')
        self.paid();auto_schedule.service_once(self.dsn)
        row=self.row();self.assertEqual(row['placement_status'],'scheduled')
        self.assertEqual(row['scheduled_start_at'],self.start)
        self.assertEqual(row['scheduled_end_at'],self.start+HOUR)
        status=self.request('GET',f"/campaigns/{self.f['campaign']}/promotion-status")
        self.assertEqual(status['automation_status'],'setup_required');self.assertFalse(status['config_applied'])
        first=(row['proxy_node_id'],row['scheduled_start_at'],row['scheduled_end_at'])
        auto_schedule.service_once(self.dsn)
        row=self.row();self.assertEqual(first,(row['proxy_node_id'],row['scheduled_start_at'],row['scheduled_end_at']))

    def test_busy_and_too_short_gaps_and_other_hold_shift_full_duration(self):
        node=self.f['node'];self.other(node,self.start);self.other(node,self.start+timedelta(minutes=90),timedelta(minutes=30))
        held=self.other(paid=False)
        with psycopg.connect(self.dsn) as c:
            c.execute("INSERT INTO slot_holds(campaign_id,proxy_node_id,starts_at,ends_at,expires_at) VALUES(%s,%s,%s,%s,now()+interval '10 minutes')",(held,node,self.start+2*HOUR,self.start+3*HOUR))
        self.paid();auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['scheduled_start_at'],self.start+3*HOUR)
        self.assertEqual(self.row()['scheduled_end_at'],self.start+4*HOUR)

    def test_prepared_nodes_choose_earliest_whole_period(self):
        second=self._management_fixture()['node'];self.bind(self.f['node']);self.bind(second)
        self.other(self.f['node'],self.start,5*HOUR);self.other(second,self.start,2*HOUR)
        self.paid();auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['proxy_node_id'],second)
        self.assertEqual(self.row()['scheduled_start_at'],self.start+2*HOUR)

    def test_long_queue_still_assigns_the_next_free_period(self):
        self.other(self.f['node'],self.start,timedelta(days=100))
        self.paid();auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['scheduled_start_at'],self.start+timedelta(days=100))
        self.assertEqual(self.row()['scheduled_end_at'],self.start+timedelta(days=100)+HOUR)

    def test_future_within_two_minutes_is_not_delayed(self):
        requested=datetime.now(timezone.utc)+timedelta(seconds=40)
        with psycopg.connect(self.dsn) as c:
            c.execute('UPDATE campaigns SET requested_start_at=%s WHERE id=%s',(requested,self.f['campaign']))
        self.paid();auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['scheduled_start_at'],requested)

    def test_old_requested_time_starts_now_with_full_duration(self):
        before=datetime.now(timezone.utc)
        with psycopg.connect(self.dsn) as c:
            c.execute("UPDATE campaigns SET requested_start_at=now()-interval '1 day' WHERE id=%s",(self.f['campaign'],))
        self.paid();auto_schedule.service_once(self.dsn)
        row=self.row();self.assertGreaterEqual(row['scheduled_start_at'],before)
        self.assertLessEqual(row['scheduled_start_at'],datetime.now(timezone.utc))
        self.assertEqual(row['scheduled_end_at']-row['scheduled_start_at'],HOUR)

    def test_existing_own_hold_is_honoured_and_consumed(self):
        with psycopg.connect(self.dsn) as c:
            c.execute("INSERT INTO slot_holds(campaign_id,proxy_node_id,starts_at,ends_at,expires_at) VALUES(%s,%s,%s,%s,now()+interval '10 minutes')",(self.f['campaign'],self.f['node'],self.start+HOUR,self.start+2*HOUR))
        self.paid();auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['scheduled_start_at'],self.start+HOUR)
        with psycopg.connect(self.dsn) as c:self.assertEqual(c.execute('SELECT count(*) FROM slot_holds WHERE campaign_id=%s',(self.f['campaign'],)).fetchone()[0],0)

    def test_admin_can_move_pause_and_resume_without_worker_overwriting(self):
        self.paid();auto_schedule.service_once(self.dsn)
        path=f"/operator/campaigns/{self.f['campaign']}"
        self.request('PATCH',path+'/slot',json={'proxy_node_id':str(self.f['node']),'start_at':(self.start+4*HOUR).isoformat()})
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['scheduled_start_at'],self.start+4*HOUR)
        self.request('DELETE',path+'/slot')
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['placement_status'],'not_scheduled')
        self.request('POST',path+'/auto-schedule');auto_schedule.service_once(self.dsn)
        self.assertEqual(self.row()['placement_status'],'scheduled')
        fixture.main.app.dependency_overrides[fixture.main.get_current_user]=lambda:{'id':self.f['owner'],'role':'advertiser'}
        self.request('PATCH',path+'/slot',403,json={'proxy_node_id':str(self.f['node']),'start_at':(self.start+6*HOUR).isoformat()})

    def test_no_nodes_waits_then_recovers_and_concurrent_workers_do_not_duplicate(self):
        with psycopg.connect(self.dsn) as c:c.execute("UPDATE proxy_nodes SET status='paused' WHERE id=%s",(self.f['node'],))
        self.paid();auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['placement_status'],'not_scheduled')
        with psycopg.connect(self.dsn) as c:c.execute("UPDATE proxy_nodes SET status='active' WHERE id=%s",(self.f['node'],))
        other=self.other();barrier=threading.Barrier(2)
        def run():
            barrier.wait();return auto_schedule.service_once(self.dsn)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
        one,two=self.row(),self.row(other)
        self.assertEqual({one['scheduled_start_at'],two['scheduled_start_at']},{self.start,self.start+HOUR})
        with psycopg.connect(self.dsn) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM audit_events WHERE campaign_id=ANY(%s) AND event_type='placement_auto_scheduled'",([self.f['campaign'],other],)).fetchone()[0],2)

    def test_waiting_forecast_moves_to_next_prepared_node_and_keeps_payment(self):
        self.paid();auto_schedule.service_once(self.dsn);before=self.row()
        second=self._management_fixture()['node'];self.bind(second)
        self.other(second,self.start,2*HOUR)
        pending=self.request('GET',f"/campaigns/{self.f['campaign']}/promotion-status")
        self.assertEqual(pending['automation_status'],'selecting_tag')
        auto_schedule.service_once(self.dsn);row=self.row()
        self.assertEqual((row['proxy_node_id'],row['scheduled_start_at'],row['scheduled_end_at']),
                         (second,self.start+2*HOUR,self.start+3*HOUR))
        self.assertEqual((row['payment_status'],row['price_kopecks_snapshot']),('verified',100))
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['scheduled_start_at'],row['scheduled_start_at'])
        with psycopg.connect(self.dsn) as conn:
            events=conn.execute("SELECT details_json FROM audit_events WHERE campaign_id=%s AND event_type='placement_auto_scheduled' ORDER BY created_at",(row['id'],)).fetchall()
            self.assertEqual(len(events),2);self.assertEqual(events[1][0]['reason'],'prepared_tag_selected')
            self.assertEqual(events[1][0]['previous_node_id'],str(before['proxy_node_id']))

    def test_late_binding_cannot_start_shortened_forecast_before_selection(self):
        from app.promotion_runtime import handle
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET requested_start_at=now()-interval '1 hour' WHERE id=%s",(self.f['campaign'],))
        self.paid();auto_schedule.service_once(self.dsn);node=self.row()['proxy_node_id'];self.bind(node)
        request=dict(host='management-'+node.hex+'.example.org',port=8443)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            plan=handle(conn,request);self.assertEqual(plan['state'],'idle')
            self.assertEqual(plan['reason'],'binding_selection_pending')
        before=datetime.now(timezone.utc);auto_schedule.service_once(self.dsn);row=self.row()
        self.assertGreaterEqual(row['scheduled_start_at'],before)
        self.assertEqual(row['scheduled_end_at']-row['scheduled_start_at'],HOUR)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            plan=handle(conn,request);self.assertEqual(plan['state'],'apply')
            self.assertEqual(plan['campaign_id'],str(row['id']))
            self.assertTrue(handle(conn,dict(request,action='ack',revision=plan['revision']))['accepted'])
        self.assertEqual(self.row()['placement_status'],'active')
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['scheduled_start_at'],row['scheduled_start_at'])

    def test_manual_move_is_preserved_even_when_other_binding_is_ready(self):
        self.paid();auto_schedule.service_once(self.dsn)
        self.request('PATCH',f"/operator/campaigns/{self.f['campaign']}/slot",json={'proxy_node_id':str(self.f['node']),'start_at':(self.start+4*HOUR).isoformat()})
        second=self._management_fixture()['node'];self.bind(second)
        # Транзакция ручного переноса могла начаться раньше фонового назначения.
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE audit_events SET created_at=created_at-interval '1 hour' WHERE campaign_id=%s AND event_type='placement_rescheduled'",(self.f['campaign'],))
        auto_schedule.service_once(self.dsn)
        self.assertEqual((self.row()['proxy_node_id'],self.row()['scheduled_start_at']),(self.f['node'],self.start+4*HOUR))
        self.assertEqual(self.request('GET',f"/campaigns/{self.f['campaign']}/promotion-status")['automation_status'],'setup_required')

    def test_stale_or_another_channels_tag_is_never_selected(self):
        self.paid();auto_schedule.service_once(self.dsn);before=self.row()
        other=self._management_fixture();self.bind(other['node'])
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE channel_promotion_bindings SET channel_url='https://t.me/wrong_channel' WHERE proxy_node_id=%s",(other['node'],))
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['proxy_node_id'],before['proxy_node_id'])
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM channel_promotion_bindings WHERE proxy_node_id=%s',(other['node'],))
        self.request('PUT','/admin/promotion-bindings',json=dict(node_id=str(other['node']),channel_id=str(other['channel']),ad_tag=uuid4().hex,telegram_binding_confirmed=True))
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['proxy_node_id'],before['proxy_node_id'])
        self.assertEqual(self.request('GET',f"/campaigns/{self.f['campaign']}/promotion-status")['automation_status'],'setup_required')

    def test_replacement_does_not_take_over_observed_or_applied_campaign(self):
        self.paid();auto_schedule.service_once(self.dsn);before=self.row()
        second=self._management_fixture()['node'];self.bind(second)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO promotion_runtime(proxy_node_id,campaign_id,state) VALUES(%s,%s,'applied')",(before['proxy_node_id'],before['id']))
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['proxy_node_id'],before['proxy_node_id'])
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM promotion_runtime WHERE campaign_id=%s',(before['id'],))
            conn.execute("INSERT INTO placement_observations(campaign_id,observer_user_id,result,method,observed_at,note) VALUES(%s,%s,'visible','separate_unsubscribed_client',now(),'Test: existing observation')",(before['id'],self.f['admin']))
        auto_schedule.service_once(self.dsn);self.assertEqual(self.row()['proxy_node_id'],before['proxy_node_id'])

    def test_concurrent_reselection_queues_without_overlaps(self):
        self.paid();second_campaign=self.other();auto_schedule.service_once(self.dsn)
        second=self._management_fixture()['node'];self.bind(second);self.other(second,self.start,2*HOUR)
        barrier=threading.Barrier(2)
        def run():barrier.wait();return auto_schedule.service_once(self.dsn)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
        rows=[self.row(),self.row(second_campaign)]
        self.assertEqual({r['proxy_node_id'] for r in rows},{second})
        self.assertEqual({r['scheduled_start_at'] for r in rows},{self.start+2*HOUR,self.start+3*HOUR})
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_events WHERE campaign_id=ANY(%s) AND details_json->>'reason'='prepared_tag_selected'",([r['id'] for r in rows],)).fetchone()[0],2)

    def test_upgrade_marks_only_matching_automatic_forecasts_once(self):
        from pathlib import Path
        self.paid();auto_schedule.service_once(self.dsn)
        manual=self.other(self.f['node'],self.start+3*HOUR)
        migration=(Path(__file__).resolve().parents[1]/'database/047_automatic_tag_selection.sql').read_text()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            try:
                conn.execute('ALTER TABLE campaigns DROP COLUMN auto_schedule_managed, DROP COLUMN auto_schedule_tag_pending')
                conn.execute(migration)
                auto=conn.execute('SELECT auto_schedule_managed,auto_schedule_tag_pending FROM campaigns WHERE id=%s',(self.f['campaign'],)).fetchone()
                self.assertEqual(auto,dict(auto_schedule_managed=True,auto_schedule_tag_pending=True))
                self.assertFalse(conn.execute('SELECT auto_schedule_managed FROM campaigns WHERE id=%s',(manual,)).fetchone()['auto_schedule_managed'])
                conn.execute('UPDATE campaigns SET auto_schedule_managed=false,auto_schedule_tag_pending=false WHERE id=%s',(self.f['campaign'],))
                conn.execute(migration)
                self.assertFalse(conn.execute('SELECT auto_schedule_managed FROM campaigns WHERE id=%s',(self.f['campaign'],)).fetchone()['auto_schedule_managed'])
            finally:
                conn.rollback()
