"""Пересчёт заявки и гонки со Stars: только в отдельной тестовой PostgreSQL."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException
from fastapi.testclient import TestClient
import test_postgres as fixture
from app.campaign_pricing import PricingChange, change_pricing
from app.bot_stars import prepare_for_user, StarsPrepareInput, StarsPrecheckoutInput, precheckout
from app.stars_checkout import read_checkout

main = fixture.main


class PricingPermissions(unittest.TestCase):
    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_admin_only_before_database_access(self):
        with TestClient(main.app) as client, patch('app.campaign_pricing.psycopg.connect') as connect:
            for role in ('advertiser', 'operator'):
                main.app.dependency_overrides[main.get_current_user] = lambda: {'id': uuid4(), 'role': role}
                path = '/api/v1/admin/campaigns/' + str(uuid4()) + '/pricing'
                for method in ('GET', 'PATCH'):
                    response = client.request(method, path, json={'revision': 'a'*64, 'discount_percent': 10, 'reason': 'Постоянный клиент'})
                    self.assertEqual(response.status_code, 403, response.text)
            connect.assert_not_called()

    def test_strict_payload_and_reason(self):
        base = dict(revision='a'*64, reason='По согласованию')
        for extra in ({}, {'discount_percent': -1}, {'discount_percent': 101},
                      {'discount_percent': 2.5}, {'discount_percent': True},
                      {'discount_percent': '10'}, {'discount_percent': 10, 'tariff_id': uuid4()},
                      {'discount_percent': 10, 'reason': '   '}):
            with self.assertRaises(ValueError):
                PricingChange(**{**base, **extra})
        self.assertEqual(PricingChange(**base, discount_percent=0).discount_percent, 0)


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class PricingIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request

    def setUp(self):
        self.admin, self.owner, self.tariff, self.next_tariff, self.channel = [uuid4() for _ in range(5)]
        self.telegram = int(uuid4().hex[:12], 16)
        with psycopg.connect(self.dsn) as conn:
            for uid, tg, role in ((self.admin, self.telegram+1, 'admin'), (self.owner, self.telegram, 'advertiser')):
                conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)', (uid, tg, role))
            for tid, name, duration, rub, stars in ((self.tariff, 'День', 86400, 30000, 151), (self.next_tariff, 'Три дня', 259200, 90000, 451)):
                conn.execute('INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,%s,%s,%s,%s)', (tid, name, duration, rub, stars))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал',%s)", (self.channel, self.owner, 'https://t.me/test_'+self.channel.hex))
        self.actor = {'id': self.admin, 'role': 'admin'}
        main.app.dependency_overrides[main.get_current_user] = lambda: self.actor
        self.addCleanup(main.app.dependency_overrides.clear)
        self.cid = self.campaign()

    def campaign(self, moderation='approved'):
        cid = uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,
                tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,
                moderation_status,payment_status) VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB',%s,%s)''',
                (cid, self.owner, self.channel, self.tariff, moderation, 'awaiting_payment' if moderation == 'approved' else 'not_required'))
        return cid

    def state(self, cid=None):
        return self.request('GET', f'/admin/campaigns/{cid or self.cid}/pricing')

    def change(self, expected=200, cid=None, **body):
        cid = cid or self.cid
        return self.request('PATCH', f'/admin/campaigns/{cid}/pricing', expected,
            json={'revision': self.state(cid)['revision'], 'reason': 'Индивидуальные условия', **body})

    def test_tariff_snapshot_hold_release_and_audit(self):
        node = uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Тест',%s,443)", (node, node.hex+'.example.org'))
            conn.execute("INSERT INTO slot_holds(campaign_id,proxy_node_id,starts_at,ends_at,expires_at) VALUES(%s,%s,now()+interval '1 day',now()+interval '2 days',now()+interval '15 minutes')", (self.cid, node))
        result = self.change(tariff_id=str(self.next_tariff))['current']
        self.assertEqual((result['name'], result['duration_seconds'], result['price_kopecks'], result['stars_price_xtr']), ('Три дня', 259200, 90000, 451))
        self.assertEqual(len(self.state()['history']), 1)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM slot_holds WHERE campaign_id=%s', (self.cid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT moderation_status,payment_status,placement_status FROM campaigns WHERE id=%s', (self.cid,)).fetchone(), ('approved', 'awaiting_payment', 'not_scheduled'))
            conn.execute('UPDATE tariffs SET stars_price_xtr=999 WHERE id=%s', (self.next_tariff,))
        with patch('app.bot_stars.stars_invoices_enabled', return_value=True):
            invoice = prepare_for_user(self.dsn, StarsPrepareInput(campaign_id=self.cid, telegram_user_id=self.telegram))
        self.assertEqual(invoice.stars_amount, 451)

    def test_replacement_discount_restore_and_moderated_free(self):
        for percent, rub, stars in ((10,27000,136), (20,24000,121), (0,30000,151)):
            current = self.change(discount_percent=percent)['current']
            self.assertEqual((current['price_kopecks'], current['stars_price_xtr']), (rub, stars))
        self.change(discount_percent=0)
        self.assertEqual(len(self.state()['history']), 3)
        pending = self.campaign('pending')
        self.change(cid=pending, discount_percent=100)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT payment_status,stars_price_snapshot FROM campaigns WHERE id=%s', (pending,)).fetchone(), ('not_required', 0))
        result = self.request('POST', f'/operator/campaigns/{pending}/moderation', json={'decision': 'approved'})
        self.assertEqual(result['payment_status'], 'verified')
        self.change(409, cid=pending, discount_percent=10)

    def test_stale_revision_tariff_change_and_role_demotion(self):
        revision = self.state()['revision']
        self.change(discount_percent=10)
        self.change(409, revision=revision, discount_percent=20)
        revision = self.state()['revision']
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE tariffs SET price_kopecks=80000 WHERE id=%s', (self.next_tariff,))
        self.change(409, revision=revision, tariff_id=str(self.next_tariff))
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE users SET role='operator' WHERE id=%s", (self.admin,))
        self.change(403, discount_percent=20)

    def test_individual_discount_replaces_promo_and_preserves_stars_snapshot(self):
        offer = uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO growth_offers(id,kind,name,percent,tariff_id) VALUES(%s,'discount','Акция',30,%s)", (offer,self.tariff))
            conn.execute('INSERT INTO growth_redemptions(campaign_id,offer_id,user_id) VALUES(%s,%s,%s)', (self.cid,offer,self.owner))
            conn.execute("""UPDATE campaigns SET pricing_version=1,base_price_kopecks_snapshot=30000,
                price_kopecks_snapshot=21000,stars_price_snapshot=101,growth_offer_id=%s,
                growth_offer_name='Акция',discount_percent_snapshot=30 WHERE id=%s""", (offer,self.cid))
        result = self.change(discount_percent=20)['current']
        self.assertEqual((result['price_kopecks'],result['stars_price_xtr']), (24000,81))
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM growth_redemptions WHERE campaign_id=%s', (self.cid,)).fetchone()[0], 0)
        self.change(tariff_id=str(self.next_tariff))
        result = self.state()['current']
        self.assertEqual((result['price_kopecks'],result['stars_price_xtr'],result['discount_percent']), (90000,451,0))

    def test_private_inactive_tariffs_and_missing_stars(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE tariffs SET admin_only=true,price_kopecks=0,stars_price_xtr=NULL WHERE id=%s', (self.next_tariff,))
        self.assertNotIn(str(self.next_tariff), [t['id'] for t in self.state()['tariffs']])
        self.change(404, tariff_id=str(self.next_tariff))
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE tariffs SET admin_only=false,price_kopecks=90000,is_active=false WHERE id=%s', (self.next_tariff,))
        self.change(404, tariff_id=str(self.next_tariff))
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE tariffs SET is_active=true WHERE id=%s', (self.next_tariff,))
            conn.execute("UPDATE campaigns SET purchase_surface='telegram' WHERE id=%s", (self.cid,))
        self.change(409, tariff_id=str(self.next_tariff))

    def attempt(self, cid, provider='telegram_stars', status='pending', expiry="now()+interval '15 minutes'", approved=False):
        with psycopg.connect(self.dsn) as conn:
            return conn.execute(f'''INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,
                currency,status,expires_at,precheckout_approved_at,provider_payment_id)
                VALUES(%s,%s,%s,%s,%s,%s,{expiry},%s,%s) RETURNING id''',
                (cid, provider, 'telegram_bot' if provider == 'telegram_stars' else 'external_web',
                 151 if provider == 'telegram_stars' else 30000, 'XTR' if provider == 'telegram_stars' else 'RUB',
                 status, '2026-01-01T00:00:00Z' if approved else None, uuid4().hex)).fetchone()[0]

    def test_invoices_block_repricing_even_with_expired_sbp_or_approved_stars(self):
        for provider, status, expiry, approved in (
            ('sbp','created','NULL',False), ('sbp','pending',"now()-interval '1 minute'",False),
            ('telegram_stars','pending','NULL',False), ('telegram_stars','pending',"now()-interval '1 minute'",True),
            ('telegram_stars','pending',"now()+interval '1 minute'",False),
            ('telegram_stars','succeeded','NULL',False), ('sbp','refunded','NULL',False)):
            with self.subTest(provider=provider, status=status, expiry=expiry, approved=approved):
                cid = self.campaign()
                self.attempt(cid, provider, status, expiry, approved)
                self.assertFalse(self.state(cid)['can_edit'])
                self.change(409, cid=cid, discount_percent=20)

    def test_expired_stars_cancelled_and_old_checkout_rejected(self):
        aid = self.attempt(self.cid, expiry="now()-interval '1 minute'")
        self.change(discount_percent=20)
        self.assertEqual(read_checkout(self.dsn, self.cid, self.owner)['amount_xtr'], 121)
        with patch.dict(os.environ, {'DATABASE_URL': self.dsn}), patch('app.bot_stars.authorize'), patch('app.bot_stars.stars_invoices_enabled', return_value=True):
            result = precheckout(StarsPrecheckoutInput(query_id='old-price', telegram_user_id=self.telegram,
                invoice_payload='stars:'+str(aid), currency='XTR', total_amount=151), main.Response())
            self.assertFalse(result.ok)
            invoice = prepare_for_user(self.dsn, StarsPrepareInput(campaign_id=self.cid, telegram_user_id=self.telegram))
            self.assertEqual(invoice.stars_amount, 121)

    def test_paid_archived_completed_and_unreviewed_proof_block(self):
        for clause in ("payment_status='verified'", "payment_status='refunded'", "archived_at=now()", "placement_status='completed'"):
            cid = self.campaign()
            with psycopg.connect(self.dsn) as conn:
                conn.execute('UPDATE campaigns SET '+clause+' WHERE id=%s', (cid,))
            self.change(409, cid=cid, discount_percent=10)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO payment_proofs(campaign_id,file_storage_key,original_file_name,content_type,size_bytes,submitted_by_user_id) VALUES(%s,'test','test.jpg','image/jpeg',1,%s)", (self.cid,self.owner))
        self.change(409, discount_percent=10)

    def test_concurrent_invoice_uses_new_price_or_prevents_repricing(self):
        # Первый порядок: администратор держит блокировку, создание счёта ждёт commit.
        state = self.state()
        started = Event()
        def invoice():
            started.set()
            return prepare_for_user(self.dsn, StarsPrepareInput(campaign_id=self.cid, telegram_user_id=self.telegram))
        with patch('app.bot_stars.stars_invoices_enabled', return_value=True), ThreadPoolExecutor(max_workers=1) as pool:
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                change_pricing(conn, self.cid, self.admin, PricingChange(revision=state['revision'], discount_percent=20, reason='Тест гонки'))
                future = pool.submit(invoice)
                self.assertTrue(started.wait(5))
            self.assertEqual(future.result(timeout=10).stars_amount, 121)
        # Обратный порядок: счёт уже создан, изменение отклоняется.
        self.change(409, discount_percent=30)
        self.assertEqual(self.state()['current']['stars_price_xtr'], 121)

    def test_migration_idempotent_and_public_report_event(self):
        with psycopg.connect(self.dsn) as conn:
            migration = (fixture.ROOT/'database/042_campaign_admin_pricing.sql').read_text()
            conn.execute(migration)
            conn.execute(migration)
        self.change(discount_percent=20)
        self.actor = {'id': self.owner, 'role': 'advertiser'}
        self.request('GET', f'/admin/campaigns/{self.cid}/pricing', 403)
        from app.placement_report import PUBLIC_EVENTS
        self.assertIn('campaign_pricing_changed', PUBLIC_EVENTS)
