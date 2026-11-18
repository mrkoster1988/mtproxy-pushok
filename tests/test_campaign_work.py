"""Реальные COMMIT/NOTIFY, независимые проверки и защита от запоздавших ответов."""
import threading
import unittest
from unittest.mock import patch

import psycopg
import test_postgres as fixture
import test_automatic_moderation as moderation_fixture
from app import automatic_moderation as ai, campaign_work, sbp_worker, auto_schedule


class WakeupTests(unittest.TestCase):
    def test_event_during_schedule_is_not_lost_before_wait(self):
        stopped, wakeup = threading.Event(), threading.Event()
        calls = []
        def work(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                wakeup.set()
            else:
                stopped.set()
                wakeup.set()
            return {'scheduled': 0}
        with patch.object(sbp_worker, 'schedule_once', side_effect=work):
            thread = threading.Thread(target=sbp_worker.schedule_loop, args=('unused', stopped, 20, wakeup))
            thread.start(); thread.join(1)
            try:
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(calls), 2)
            finally:
                stopped.set(); wakeup.set(); thread.join(2)

    def test_retry_backoff_is_short_initially_and_bounded(self):
        self.assertEqual([ai.retry_seconds(n) for n in (1, 2, 3, 4, 5, 1000)], [30, 60, 120, 240, 300, 300])


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен тестовый PostgreSQL')
class CampaignWorkIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request
    db = moderation_fixture.AutomaticRejectionIntegration.db
    act = moderation_fixture.AutomaticRejectionIntegration.act
    verify = moderation_fixture.AutomaticRejectionIntegration.verify
    campaign = moderation_fixture.AutomaticRejectionIntegration.campaign
    pending = moderation_fixture.AutomaticRejectionIntegration.pending
    ai_result = moderation_fixture.AutomaticRejectionIntegration.ai_result

    def setUp(self):
        moderation_fixture.AutomaticRejectionIntegration.setUp(self)
        self.db('UPDATE campaigns SET archived_at=now() WHERE archived_at IS NULL')

    def listener(self):
        connection = psycopg.connect(self.dsn, autocommit=True)
        connection.execute('LISTEN pushok_campaign_work')
        self.addCleanup(connection.close)
        return connection

    def notices(self, connection):
        return [item.payload for item in connection.notifies(timeout=0.05)]

    def approve(self, cid):
        self.act(self.admin)
        return self.request('POST', f'/operator/campaigns/{cid}/moderation', json={'decision':'approved'})

    def test_payment_last_notifies_only_after_commit_and_duplicate_does_not(self):
        cid = self.pending()
        listener = self.listener()
        self.approve(cid)
        self.assertEqual(self.notices(listener), [])
        with psycopg.connect(self.dsn) as connection:
            connection.execute("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
            self.assertEqual(self.notices(listener), [])
        self.assertEqual(self.notices(listener), ['schedule'])
        self.db("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
        self.assertEqual(self.notices(listener), [])

    def test_rollback_does_not_schedule(self):
        cid = self.pending()
        self.approve(cid)
        listener = self.listener()
        with psycopg.connect(self.dsn) as connection:
            connection.execute("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
            connection.rollback()
        self.assertEqual(self.notices(listener), [])

    def test_moderation_last_preserves_already_verified_payment(self):
        cid = self.pending()
        self.db("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
        listener = self.listener()
        self.assertEqual(self.approve(cid)['payment_status'], 'verified')
        self.assertEqual(self.notices(listener), ['schedule'])

    def test_free_campaign_schedules_on_approval_and_pending_wakes_ai(self):
        listener = self.listener()
        cid = self.pending()
        self.assertIn('moderation', self.notices(listener))
        self.db('UPDATE campaigns SET price_kopecks_snapshot=0 WHERE id=%s', (cid,))
        self.assertEqual(self.approve(cid)['payment_status'], 'verified')
        self.assertIn('schedule', self.notices(listener))

    def test_paid_campaign_is_planned_on_committed_wakeup(self):
        cid = self.pending()
        self.approve(cid)
        self.db("INSERT INTO proxy_nodes(name,host,port) VALUES('Тест','test.example.org',8443)")
        stopped, schedule, moderation = threading.Event(), threading.Event(), threading.Event()
        thread = threading.Thread(target=campaign_work.listen, args=(self.dsn, stopped, schedule, [moderation]))
        thread.start()
        try:
            self.assertTrue(schedule.wait(2))  # LISTEN установлен.
            self.assertTrue(moderation.is_set())
            schedule.clear(); moderation.clear()
            self.db("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
            self.assertTrue(schedule.wait(2))
            auto_schedule.service_once(self.dsn)
            row = self.db('SELECT placement_status,scheduled_start_at,scheduled_end_at FROM campaigns WHERE id=%s', (cid,))[0]
            self.assertEqual(row['placement_status'], 'scheduled')
            self.assertEqual((row['scheduled_end_at'] - row['scheduled_start_at']).total_seconds(), 86400)
            self.assertFalse(moderation.is_set())
        finally:
            stopped.set(); thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_temporary_failure_retries_at_due_time_and_exposes_eta(self):
        cid = self.pending()
        with self.ai_result() as classify:
            classify.side_effect = TimeoutError()
            self.assertEqual(ai.service_once(self.dsn, limit=1)['retry'], 1)
            row = self.db('SELECT * FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]
            self.assertEqual(row['attempts'], 1)
            self.assertIsNone(row['claim_token'])
            self.assertIsNone(row['lease_until'])
            self.assertGreaterEqual((row['retry_at'] - row['attempted_at']).total_seconds(), 30)
            self.assertLess((row['retry_at'] - row['attempted_at']).total_seconds(), 35)
            card = next(item for item in self.request('GET', '/campaigns') if item['id'] == cid)
            self.assertEqual(card['moderation_check'], 'retry')
            self.assertTrue(card['moderation_retry_at'])
            self.assertEqual(ai.service_once(self.dsn, limit=1)['retry'], 0)
            self.db("UPDATE ai_moderation_checks SET retry_at=now()-interval '1 second' WHERE campaign_id=%s", (cid,))
            self.assertEqual(ai.service_once(self.dsn, limit=1)['retry'], 1)
            row = self.db('SELECT attempts,retry_at,attempted_at FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]
            self.assertEqual(row['attempts'], 2)
            self.assertGreaterEqual((row['retry_at'] - row['attempted_at']).total_seconds(), 60)

    def test_parallel_worker_skips_leased_campaign_and_finishes_another(self):
        first, second = self.pending(), self.pending()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def result(row, config):
            if str(row['id']) == first:
                entered.set()
                if not release.wait(5): raise RuntimeError('test_release_timeout')
            return 'review', 'Требуется проверка медиа сотрудником.', '', 'none', []
        def run():
            try: ai.service_once(self.dsn, limit=1)
            except Exception as error: errors.append(error)
        with self.ai_result(), patch.object(ai, 'moderation_result', side_effect=result):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                card = next(item for item in self.request('GET', '/campaigns') if item['id'] == first)
                self.assertEqual(card['moderation_check'], 'processing')
                self.assertIsNone(card['moderation_retry_at'])
                self.act(self.admin)
                check = self.request('GET', '/community/moderation/' + first)['check']
                self.assertEqual(check['decision'], 'processing')
                self.assertIn('ИИ проверяет', check['reason'])
                self.assertEqual(ai.service_once(self.dsn, limit=1)['review'], 1)
                self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s', (second,))[0]['decision'], 'review')
                self.assertEqual(ai.service_once(self.dsn, limit=1)['review'], 0)
            finally:
                release.set(); thread.join(3)
            self.assertFalse(errors)
            self.assertEqual(ai.service_once(self.dsn, limit=1)['review'], 0)
        self.assertEqual(len(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id IN (%s,%s)', (first, second))), 2)

    def test_expired_claim_is_recovered_and_old_response_cannot_overwrite(self):
        cid = self.pending()
        entered, release = threading.Event(), threading.Event()
        results = []
        def result(row, config):
            if not entered.is_set():
                entered.set()
                if not release.wait(5): raise RuntimeError('test_release_timeout')
                return 'approved', 'Старый ответ', '', 'none', []
            return 'review', 'Новое решение', '', 'none', []
        with self.ai_result(), patch.object(ai, 'moderation_result', side_effect=result):
            thread = threading.Thread(target=lambda: results.append(ai.service_once(self.dsn, limit=1)))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.db("UPDATE ai_moderation_checks SET lease_until=now()-interval '1 second',retry_at=now()-interval '1 second' WHERE campaign_id=%s", (cid,))
                self.assertEqual(ai.service_once(self.dsn, limit=1)['review'], 1)
            finally:
                release.set(); thread.join(3)
        self.assertEqual(results[0]['approved'], 0)
        self.assertEqual(self.db('SELECT reason FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]['reason'], 'Новое решение')
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
        self.assertEqual(len(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id=%s', (cid,))), 1)

    def test_ai_approval_preserves_payment_and_wakes_scheduler(self):
        cid = self.pending()
        self.db("UPDATE campaigns SET payment_status='verified' WHERE id=%s", (cid,))
        listener = self.listener()
        posts = ['Понятный подробный урок о таблицах и формулах. ' * 4] * 3
        with self.ai_result(ai.Decision(decision='approve', reason='Обычный канал'), fully_textual=True, posts=posts, title='Уроки'):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['approved'], 1)
        self.assertEqual(self.db('SELECT payment_status FROM campaigns WHERE id=%s', (cid,))[0]['payment_status'], 'verified')
        self.assertEqual(self.notices(listener), ['schedule'])

    def test_response_past_lease_without_new_worker_is_not_applied(self):
        cid = self.pending()
        def expired(row, config):
            self.db("UPDATE ai_moderation_checks SET lease_until=now()-interval '1 second',retry_at=now()-interval '1 second' WHERE campaign_id=%s", (cid,))
            return 'approved', 'Просроченный ответ', '', 'none', []
        with self.ai_result(), patch.object(ai, 'moderation_result', side_effect=expired):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['approved'], 0)
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
        with self.ai_result(), patch.object(ai, 'moderation_result', return_value=('review', 'Проверка сотрудником', '', 'none', [])):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['review'], 1)
