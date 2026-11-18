"""Внешняя привязка: очередь, запрет раннего запуска, повтор и полный период."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import telegram_binding as binding, promotion_runtime, auto_schedule


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class BindingQueueTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    _management_fixture=fixture.PostgresIntegration._management_fixture
    request=fixture.PostgresIntegration.request

    def setUp(self):
        self.f=self._management_fixture(ready=True)
        self.tag=uuid4().hex
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.node=conn.execute('SELECT * FROM proxy_nodes WHERE id=%s',(self.f['node'],)).fetchone()
            conn.execute("UPDATE sponsor_channels SET telegram_url='https://t.me/sample_channel' WHERE id=%s",(self.f['channel'],))
        self.base=dict(host=self.node['host'],port=self.node['port'])
        self.proof=dict(tag=self.tag,channel_username='sample_channel',owner_telegram_id=999,
                        telegram_channel_id=777,bot_message_id=555)

    def exchange(self,action,base=None,**extra):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            return binding.handle(conn,dict(base or self.base,action=action,**extra))

    def claim(self):
        job=self.exchange('owner_claim');self.assertEqual(job['state'],'job')
        return job['job_id']

    def authorize(self,job):
        self.assertTrue(self.exchange('owner_authorize',job_id=job,**self.proof)['accepted'])

    def mature(self,job):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE telegram_binding_jobs SET retry_at=now()-interval '1 second',verified_at=now()-interval '1 hour' WHERE id=%s",(job,))

    def save_manual(self, expected=200, **changes):
        fixture.main.app.dependency_overrides[fixture.main.get_current_user] = lambda: dict(id=self.f['admin'], role='admin')
        self.addCleanup(fixture.main.app.dependency_overrides.clear)
        return self.request('PUT', '/admin/promotion-bindings', expected,
            json=dict(dict(node_id=str(self.node['id']), channel_id=str(self.f['channel']),
                           ad_tag=self.tag, telegram_binding_confirmed=True), **changes))

    def test_manual_binding_replaces_failed_inspection_and_rejects_stale_worker(self):
        job = self.claim()
        self.exchange('owner_error', job_id=job, code='proxy_not_found')
        self.assertTrue(self.save_manual()['saved'])
        for action in ('owner_authorize', 'owner_verified', 'owner_error'):
            self.assertFalse(self.exchange(action, job_id=job, code='proxy_not_found', **self.proof)['accepted'])
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM telegram_binding_jobs WHERE id=%s', (job,)).fetchone())
            saved = conn.execute('SELECT * FROM channel_promotion_bindings WHERE proxy_node_id=%s', (self.node['id'],)).fetchone()
            self.assertEqual(saved['confirmed_by'], self.f['admin'])
            audit = conn.execute("SELECT details_json FROM audit_events WHERE event_type='promotion_binding_confirmed' AND details_json->>'replaced_inspection_id'=%s", (job,)).fetchone()
            self.assertIsNotNone(audit)
            self.assertNotIn(self.tag, str(audit))
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            self.assertEqual(promotion_runtime.node_plan(conn, self.node)['reason'], 'waiting_start')

    def test_manual_binding_cannot_interrupt_running_or_uncertain_external_change(self):
        job = self.claim()
        self.assertEqual(self.save_manual(409)['detail'], 'promotion_binding_busy')
        self.authorize(job)
        self.exchange('owner_error', job_id=job, code='bot_response_timeout')
        self.assertEqual(self.save_manual(409)['detail'], 'promotion_binding_busy')
        self.exchange('owner_verified', job_id=job, **self.proof)
        self.exchange('owner_error', job_id=job, code='telegram_unavailable')
        self.assertEqual(self.save_manual(409)['detail'], 'promotion_binding_busy')
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT phase FROM telegram_binding_jobs WHERE id=%s', (job,)).fetchone()[0], 'propagating')
            self.assertIsNone(conn.execute('SELECT 1 FROM channel_promotion_bindings WHERE proxy_node_id=%s', (self.node['id'],)).fetchone())

    def test_failed_manual_save_preserves_inspection_and_duplicate_tag_has_code(self):
        job = self.claim()
        self.exchange('owner_error', job_id=job, code='proxy_not_found')
        other = self._management_fixture()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                SELECT id,%s,'https://t.me/other_channel',host,port,%s,%s FROM proxy_nodes WHERE id=%s''',
                (other['channel'], self.tag, other['admin'], other['node']))
        self.assertEqual(self.save_manual(409)['detail'], 'promotion_tag_already_bound')
        self.assertEqual(self.save_manual(422, telegram_binding_confirmed=False)['detail'], 'telegram_binding_confirmation_required')
        self.save_manual(404, channel_id=str(uuid4()))
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT phase,error_code FROM telegram_binding_jobs WHERE id=%s', (job,)).fetchone(), ('inspect', 'proxy_not_found'))

    def test_manual_save_waits_for_scheduler_lock_without_changing_job(self):
        job = self.claim()
        self.exchange('owner_error', job_id=job, code='proxy_not_found')
        with psycopg.connect(self.dsn) as conn:
            conn.execute('SELECT pg_advisory_xact_lock(736102482)')
            self.assertEqual(self.save_manual(409)['detail'], 'promotion_binding_scheduler_busy')
            self.assertEqual(conn.execute('SELECT phase FROM telegram_binding_jobs WHERE id=%s', (job,)).fetchone()[0], 'inspect')
        self.assertTrue(self.save_manual()['saved'])

    def test_manual_channel_is_not_immediately_blocked_by_default_sponsor(self):
        other = self._management_fixture()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('INSERT INTO default_sponsor_settings(singleton,channel_id,updated_by) VALUES(true,%s,%s) ON CONFLICT(singleton) DO UPDATE SET channel_id=EXCLUDED.channel_id', (other['channel'], self.f['admin']))
            job = conn.execute('''INSERT INTO telegram_binding_jobs(proxy_node_id,channel_id,channel_url,node_host,node_port,error_code)
                VALUES(%s,%s,'https://t.me/default_channel',%s,%s,'proxy_not_found') RETURNING id''', (self.node['id'], other['channel'], self.node['host'], self.node['port'])).fetchone()[0]
        def cleanup():
            with psycopg.connect(self.dsn) as conn:
                conn.execute('DELETE FROM default_sponsor_settings')
        self.addCleanup(cleanup)
        self.save_manual()
        # До прохода планировщика агент не должен вновь заблокировать узел.
        self.assertEqual(self.exchange('owner_claim')['state'], 'wait')
        self.assertFalse(self.exchange('owner_authorize', job_id=str(job), **self.proof)['accepted'])
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            self.assertEqual(promotion_runtime.node_plan(conn, self.node)['state'], 'idle')
            campaign = conn.execute('SELECT proxy_node_id,scheduled_start_at FROM campaigns WHERE id=%s', (self.f['campaign'],)).fetchone()
            self.assertEqual(campaign['proxy_node_id'], self.node['id'])
            self.assertGreater(campaign['scheduled_start_at'], datetime.now(timezone.utc))

    def test_verified_binding_reassigns_full_period_and_duplicate_is_harmless(self):
        auto_schedule.service_once(self.dsn)
        job=self.claim();self.assertEqual(job,self.claim())
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(promotion_runtime.node_plan(conn,self.node)['reason'],'telegram_binding_pending')
        self.authorize(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        self.assertFalse(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        with psycopg.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM channel_promotion_bindings WHERE proxy_node_id=%s',(self.node['id'],)).fetchone())
        self.mature(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.assertFalse(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        before=datetime.now(timezone.utc)
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            row=conn.execute('SELECT * FROM campaigns WHERE id=%s',(self.f['campaign'],)).fetchone()
            self.assertEqual(row['proxy_node_id'],self.node['id'])
            self.assertGreaterEqual(row['scheduled_start_at'],before)
            self.assertEqual((row['scheduled_end_at']-row['scheduled_start_at']).total_seconds(),3600)
            self.assertFalse(row['auto_schedule_tag_pending'])
            self.assertEqual(promotion_runtime.node_plan(conn,self.node)['state'],'apply')
            self.assertNotIn(self.tag,str(binding.public_status(conn)))

    def test_empty_card_after_one_hour_rebinds_and_restarts_the_full_window(self):
        job = self.claim()
        self.authorize(job)
        self.assertTrue(self.exchange('owner_verified', job_id=job, **self.proof)['accepted'])
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute("""SELECT j.verified_at,c.scheduled_start_at,c.scheduled_end_at
                FROM telegram_binding_jobs j JOIN campaigns c ON c.id=j.campaign_id WHERE j.id=%s""", (job,)).fetchone()
            self.assertEqual(row['scheduled_start_at'] - row['verified_at'], timedelta(hours=1))
            self.assertEqual(row['scheduled_end_at'] - row['scheduled_start_at'], timedelta(hours=1))
            self.assertEqual(promotion_runtime.node_plan(conn, self.node)['state'], 'idle')
        self.mature(job)
        unknown = dict(self.proof,channel_username=None)
        self.assertFalse(self.exchange('owner_verified', job_id=job, **unknown)['accepted'])
        self.assertTrue(self.exchange('owner_verified', job_id=job, promotion_empty=True, **unknown)['accepted'])
        self.assertEqual(self.exchange('owner_claim')['phase'], 'bind')
        self.assertTrue(self.exchange('owner_verified', job_id=job, **self.proof)['accepted'])
        self.assertEqual(self.exchange('owner_claim')['state'], 'wait')
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            public = next(j for j in binding.public_status(conn) if str(j['campaign_id']) == str(self.f['campaign']))
            self.assertGreater(public['planned_start_at'], row['scheduled_start_at'])
            self.assertEqual(public['planned_start_at'] - public['verified_at'], timedelta(hours=1))

    def test_empty_cached_card_reopens_preparation_for_the_scheduled_campaign(self):
        self.save_manual()
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE channel_promotion_bindings SET confirmed_at=now()-interval '1 day' WHERE ad_tag=%s", (self.tag,))
        probe = self.exchange('owner_claim', probe_cached=True)
        self.assertEqual(probe['state'], 'probe')
        self.assertEqual(self.exchange('owner_claim', probe_cached=True)['state'], 'wait')
        report = dict(probe_cached=True, probe_at=probe['probe_at'], channel_id=probe['channel_id'])
        self.assertFalse(self.exchange('owner_empty_cached', tag=uuid4().hex, **report)['accepted'])
        self.assertTrue(self.exchange('owner_empty_cached', tag=self.tag, **report)['accepted'])
        self.assertEqual(self.exchange('owner_claim', probe_cached=True)['phase'], 'inspect')

    def test_active_and_manual_scheduled_node_are_not_rebound(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET proxy_node_id=%s,placement_status='active',scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '1 hour' WHERE id=%s",(self.node['id'],self.f['campaign']))
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='scheduled',scheduled_start_at=now()+interval '7 days',scheduled_end_at=now()+interval '7 days 1 hour' WHERE id=%s",(self.f['campaign'],))
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')

    def blocked_forecast_queue(self):
        """Текущему каналу нет тега, следующие 3+3 часа заняты готовым каналом."""
        future = [self._management_fixture(ready=True) for _ in range(2)]
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE proxy_nodes SET status='paused' WHERE id=ANY(%s)", ([f['node'] for f in future],))
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                SELECT %s,id,telegram_url,%s,%s,%s,%s FROM sponsor_channels WHERE id=%s''',
                (self.node['id'],self.node['host'],self.node['port'],self.tag,self.f['admin'],future[0]['channel']))
            for index, f in enumerate([self.f, *future]):
                conn.execute('''UPDATE campaigns SET placement_status='scheduled',proxy_node_id=%s,
                    auto_schedule_managed=true,auto_schedule_tag_pending=%s,duration_seconds_snapshot=10800,
                    requested_start_at=now()-interval '2 hours'+%s*interval '3 hours',
                    scheduled_start_at=now()-interval '2 hours'+%s*interval '3 hours',
                    scheduled_end_at=now()+interval '1 hour'+%s*interval '3 hours' WHERE id=%s''',
                    (self.node['id'],index==0,index,index,index,f['campaign']))
            conn.execute('UPDATE campaigns SET channel_id=%s WHERE id=%s',(future[0]['channel'],future[1]['campaign']))
        return future

    def test_earlier_unprepared_campaign_is_not_blocked_by_later_prepared_forecasts(self):
        future = self.blocked_forecast_queue()
        job = self.claim()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(conn.execute('SELECT campaign_id FROM telegram_binding_jobs WHERE id=%s',(job,)).fetchone()['campaign_id'],self.f['campaign'])
        # Планировщик не возвращает прогнозы на узел, пока Telegram меняет канал.
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM campaigns WHERE proxy_node_id=%s AND placement_status='scheduled'",(self.node['id'],)).fetchone()[0],0)
        self.authorize(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.mature(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        before = datetime.now(timezone.utc)
        auto_schedule.service_once(self.dsn)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            rows=conn.execute('SELECT * FROM campaigns WHERE id=ANY(%s) ORDER BY scheduled_start_at',([self.f['campaign'],*[f['campaign'] for f in future]],)).fetchall()
            self.assertEqual(rows[0]['id'],self.f['campaign'])
            self.assertGreaterEqual(rows[0]['scheduled_start_at'],before)
            for row in rows:
                self.assertEqual((row['scheduled_end_at']-row['scheduled_start_at']).total_seconds(),10800)
            for previous,current in zip(rows,rows[1:]):
                self.assertGreaterEqual(current['scheduled_start_at'],previous['scheduled_end_at'])
            self.assertEqual(promotion_runtime.node_plan(conn,self.node)['state'],'apply')

    def test_blocked_queue_preserves_manual_active_paused_observed_and_applied_campaigns(self):
        future = self.blocked_forecast_queue()
        with psycopg.connect(self.dsn) as conn:
            original = conn.execute('SELECT id,placement_status,proxy_node_id,scheduled_start_at,scheduled_end_at FROM campaigns WHERE proxy_node_id=%s ORDER BY id',(self.node['id'],)).fetchall()
        changes = [
            ("UPDATE campaigns SET auto_schedule_managed=false WHERE id=%s", "UPDATE campaigns SET auto_schedule_managed=true WHERE id=%s"),
            ("UPDATE campaigns SET placement_status='active' WHERE id=%s", "UPDATE campaigns SET placement_status='scheduled' WHERE id=%s"),
            ("UPDATE campaigns SET auto_schedule_paused=true WHERE id=%s", "UPDATE campaigns SET auto_schedule_paused=false WHERE id=%s"),
        ]
        for change, restore in changes:
            with self.subTest(change=change):
                with psycopg.connect(self.dsn) as conn:conn.execute(change,(future[0]['campaign'],))
                self.assertEqual(self.exchange('owner_claim')['state'],'wait')
                with psycopg.connect(self.dsn) as conn:conn.execute(restore,(future[0]['campaign'],))
        with psycopg.connect(self.dsn) as conn:
            observation=conn.execute("""INSERT INTO placement_observations(campaign_id,observer_user_id,result,method,observed_at,note)
                VALUES(%s,%s,'not_visible','separate_unsubscribed_client',now(),'Protected operator observation') RETURNING id""",(self.f['campaign'],self.f['admin'])).fetchone()[0]
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM placement_observations WHERE id=%s',(observation,))
            conn.execute("INSERT INTO promotion_runtime(proxy_node_id,campaign_id,state) VALUES(%s,%s,'applied')",(self.node['id'],self.f['campaign']))
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM promotion_runtime WHERE proxy_node_id=%s',(self.node['id'],))
            self.assertEqual(conn.execute('SELECT id,placement_status,proxy_node_id,scheduled_start_at,scheduled_end_at FROM campaigns WHERE proxy_node_id=%s ORDER BY id',(self.node['id'],)).fetchall(),original)
            self.assertIsNone(conn.execute('SELECT 1 FROM telegram_binding_jobs WHERE proxy_node_id=%s',(self.node['id'],)).fetchone())

    def test_blocked_queue_preserves_live_reservation(self):
        future = self.blocked_forecast_queue()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO slot_holds(campaign_id,proxy_node_id,starts_at,ends_at,expires_at)
                VALUES(%s,%s,now()+interval '9 hours',now()+interval '12 hours',now()+interval '10 minutes')''',
                (future[0]['campaign'],self.node['id']))
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM campaigns WHERE proxy_node_id=%s AND placement_status='scheduled'",(self.node['id'],)).fetchone()[0],3)

    def test_preparation_reserves_queue_before_next_forecast_enters_protected_window(self):
        self.blocked_forecast_queue()
        with psycopg.connect(self.dsn) as conn:
            for campaign, in conn.execute('SELECT id FROM campaigns WHERE proxy_node_id=%s ORDER BY scheduled_start_at DESC',(self.node['id'],)).fetchall():
                conn.execute("""UPDATE campaigns SET requested_start_at=requested_start_at+interval '90 minutes',
                    scheduled_start_at=scheduled_start_at+interval '90 minutes',scheduled_end_at=scheduled_end_at+interval '90 minutes'
                    WHERE id=%s""",(campaign,))
        job=self.claim()
        auto_schedule.service_once(self.dsn)
        self.authorize(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.mature(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM campaigns WHERE proxy_node_id=%s AND placement_status='scheduled'",(self.node['id'],)).fetchone()[0],1)

    def test_prepared_current_campaign_keeps_its_schedule(self):
        self.blocked_forecast_queue()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                VALUES(%s,%s,'https://t.me/sample_channel',%s,%s,%s,%s)''',
                (self.node['id'],self.f['channel'],self.node['host'],self.node['port'],uuid4().hex,self.f['admin']))
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM campaigns WHERE proxy_node_id=%s AND placement_status='scheduled'",(self.node['id'],)).fetchone()[0],3)

    def test_shared_tag_is_rejected_before_external_write(self):
        other=self._management_fixture()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                SELECT n.id,%s,'https://t.me/other_channel',n.host,n.port,%s,%s FROM proxy_nodes n WHERE n.id=%s''',
                (other['channel'],self.tag,other['admin'],other['node']))
        job=self.claim()
        self.assertFalse(self.exchange('owner_authorize',job_id=job,**self.proof)['accepted'])

    def test_old_binding_removed_before_mutation_and_flood_wait_survives_retry(self):
        old=self._management_fixture()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                VALUES(%s,%s,'https://t.me/old_channel',%s,8443,%s,%s)''',(self.node['id'],old['channel'],self.node['host'],self.tag,old['admin']))
        job=self.claim();self.authorize(job)
        self.exchange('owner_error',job_id=job,code='telegram_flood_wait',retry_seconds=7200)
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM channel_promotion_bindings WHERE ad_tag=%s',(self.tag,)).fetchone())
            row=conn.execute('SELECT retry_at>now()+interval \'119 minutes\' AS waiting FROM telegram_binding_jobs WHERE id=%s',(job,)).fetchone()
            self.assertTrue(row['waiting'])
            self.assertEqual(promotion_runtime.node_plan(conn,self.node)['reason'],'telegram_binding_pending')

    def test_cancelled_or_renamed_target_cannot_publish_stale_success(self):
        job=self.claim();self.authorize(job)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE sponsor_channels SET telegram_url='https://t.me/changed_channel' WHERE id=%s",(self.f['channel'],))
        self.assertFalse(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM channel_promotion_bindings WHERE ad_tag=%s',(self.tag,)).fetchone())

    def test_wrong_account_channel_tag_and_job_never_confirm(self):
        job=self.claim();self.authorize(job)
        for changes in [dict(tag=uuid4().hex),dict(owner_telegram_id=1),dict(channel_username='another_channel'),dict(bot_message_id=-1)]:
            self.assertFalse(self.exchange('owner_verified',job_id=job,**dict(self.proof,**changes))['accepted'])
        self.assertFalse(self.exchange('owner_verified',job_id=str(uuid4()),**self.proof)['accepted'])

    def test_two_free_hosts_claim_campaign_once(self):
        other=self._management_fixture()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            other_node=conn.execute('SELECT * FROM proxy_nodes WHERE id=%s',(other['node'],)).fetchone()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda base:self.exchange('owner_claim',base),[self.base,dict(host=other_node['host'],port=8443)]))
        self.assertEqual(sum(r['state']=='job' for r in results),1)

    def test_free_host_can_beat_busy_node_with_prepared_tag(self):
        other=self._management_fixture()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,channel_url,node_host,node_port,ad_tag,confirmed_by)
                SELECT n.id,%s,'https://t.me/sample_channel',n.host,n.port,%s,%s FROM proxy_nodes n WHERE n.id=%s''',
                (self.f['channel'],uuid4().hex,other['admin'],other['node']))
        # Существующая готовая связь свободна прямо сейчас: подготовка лишняя.
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='active',proxy_node_id=%s,scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '10 hours' WHERE id=%s",(other['node'],other['campaign']))
        # Та же связь занята надолго: свободный узел подготовит канал раньше.
        self.claim()

    def test_reserved_node_rejects_manual_slot_and_public_status_hides_tag(self):
        from datetime import timedelta
        job=self.claim();self.authorize(job)
        fixture.main.app.dependency_overrides[fixture.main.get_current_user]=lambda:dict(id=self.f['admin'],role='admin')
        self.request('POST',f"/operator/campaigns/{self.f['campaign']}/schedule",409,
            json=dict(proxy_node_id=str(self.node['id']),start_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()))
        data=self.request('GET','/admin/promotion-bindings')
        self.assertEqual(next(r for r in data['telegram_automation'] if r['proxy_node_id']==str(self.node['id']))['phase'],'bind')
        self.assertNotIn(self.tag,str(data))
        self.assertEqual(self.request('GET',f"/campaigns/{self.f['campaign']}/promotion-status")['automation_status'],'preparing_tag')

    def test_migration_replay_preserves_verified_binding_and_history(self):
        from pathlib import Path
        job=self.claim();self.authorize(job)
        self.exchange('owner_verified',job_id=job,**self.proof);self.mature(job)
        self.exchange('owner_verified',job_id=job,**self.proof)
        with psycopg.connect(self.dsn) as conn:
            for migration in sorted((Path(__file__).resolve().parents[1]/'database').glob('0*.sql'))[1:]:
                conn.execute(migration.read_text())
            self.assertEqual(conn.execute('SELECT count(*) FROM telegram_binding_events WHERE job_id=%s',(job,)).fetchone()[0],1)
            self.assertIsNone(conn.execute('SELECT confirmed_by FROM channel_promotion_bindings WHERE ad_tag=%s',(self.tag,)).fetchone()[0])

    def test_default_channel_is_prepared_again_when_queue_empty(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='cancelled' WHERE id=%s",(self.f['campaign'],))
            conn.execute('INSERT INTO default_sponsor_settings(singleton,channel_id,updated_by) VALUES(true,%s,%s) ON CONFLICT(singleton) DO UPDATE SET channel_id=EXCLUDED.channel_id', (self.f['channel'],self.f['admin']))
        def cleanup():
            with psycopg.connect(self.dsn) as conn:conn.execute('DELETE FROM default_sponsor_settings')
        self.addCleanup(cleanup)
        job=self.claim();self.authorize(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        self.mature(job)
        self.assertTrue(self.exchange('owner_verified',job_id=job,**self.proof)['accepted'])
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertEqual(promotion_runtime.node_plan(conn,self.node)['state'],'default')

    def test_channel_failure_is_visible_without_claiming_active_preparation(self):
        from app import promotion_diagnostics
        from unittest.mock import patch
        job=self.claim()
        self.exchange('owner_error',job_id=job,code='channel_not_broadcast')
        report=dict(code='telegram_binding_pending',recent=True,checked_at=datetime.now(timezone.utc),
                    node_id=str(self.node['id']),host=self.node['host'],port=self.node['port'])
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn, patch.object(promotion_diagnostics,'read_report',return_value=report):
            row=next(row for row in binding.public_status(conn) if row['proxy_node_id']==self.node['id'])
            self.assertEqual(row['error_code'],'channel_not_broadcast')
            self.assertEqual(row['channel_url'],'https://t.me/sample_channel')
            result=promotion_diagnostics.diagnostics(conn)
            node=next(row for row in result['nodes'] if row['node_id']==str(self.node['id']))
            self.assertEqual(node['title'],'По ссылке находится не канал')
            self.assertEqual(result['agent']['title'],node['title'])
            self.assertFalse(node['config_applied'])
            self.assertFalse(result['display_verified'])
            self.assertNotIn(self.tag,str(result))
            report['recent']=False
            self.assertEqual(promotion_diagnostics.diagnostics(conn)['agent']['title'],'Отчёт агента устарел')
        self.assertEqual(self.exchange('owner_claim')['state'],'wait')

    def test_propagation_is_waiting_not_an_error_or_active_binding(self):
        from app import promotion_diagnostics
        job=self.claim();self.authorize(job)
        self.exchange('owner_verified',job_id=job,**self.proof)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            result=promotion_diagnostics.diagnostics(conn)
            node=next(row for row in result['nodes'] if row['node_id']==str(self.node['id']))
            self.assertEqual(node['title'],'Привязка проверена — ожидаем обновления Telegram')
            self.assertFalse(node['config_applied'])
