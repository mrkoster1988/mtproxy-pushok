"""Интеграция с отдельной временной БД. TEST_ADMIN_DSN — только тестовый PostgreSQL."""
import base64
import hashlib
import hmac
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
from app import main


def _approved_test_postgres():
    """Интеграция создаёт и удаляет БД; запрещена без явного opt-in.

    Предостережение: локальный порт может быть туннелем к production.
    Проверяйте сам сервер PostgreSQL перед включением маркера.
    """
    from urllib.parse import urlsplit
    dsn = os.getenv("TEST_ADMIN_DSN", "")
    if os.getenv("PUSHOK_TEST_DB_ALLOWED") != "1":
        return False
    if not dsn.startswith(("postgresql://", "postgres://")):
        return False
    try:
        parsed = urlsplit(dsn)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        ci = (os.getenv("CI") == "true" or os.getenv("GITLAB_CI") == "true")
        return ((local or (ci and parsed.hostname == "postgres"))
                and parsed.path == "/postgres" and bool(parsed.username)
                and parsed.password is not None and not parsed.fragment)
    except ValueError:
        return False


@unittest.skipUnless(_approved_test_postgres(),
    "Только тестовый PostgreSQL с PUSHOK_TEST_DB_ALLOWED=1 и TEST_ADMIN_DSN")
class PostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_dsn = os.environ["TEST_ADMIN_DSN"]
        cls.dbname = "pushok_test_" + uuid4().hex
        for attempt in range(30):
            try:
                with psycopg.connect(cls.admin_dsn, autocommit=True) as conn:
                    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.dbname)))
                break
            except psycopg.OperationalError:
                if attempt == 29:
                    raise
                time.sleep(1)
        cls.dsn = make_conninfo(cls.admin_dsn, dbname=cls.dbname)
        cls.old_dsn, cls.old_storage, cls.old_evidence_storage = (
            main.DATABASE_URL, main.PAYMENT_PROOFS_DIR, main.PLACEMENT_EVIDENCE_DIR
        )
        cls.storage = tempfile.TemporaryDirectory()
        main.DATABASE_URL = cls.dsn
        main.PAYMENT_PROOFS_DIR = cls.storage.name
        main.PLACEMENT_EVIDENCE_DIR = str(Path(cls.storage.name) / "placement-evidence")
        with psycopg.connect(cls.dsn) as conn:
            for migration in sorted((ROOT / "database").glob("*.sql")):
                conn.execute(migration.read_text())
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        main.DATABASE_URL, main.PAYMENT_PROOFS_DIR, main.PLACEMENT_EVIDENCE_DIR = (
            cls.old_dsn, cls.old_storage, cls.old_evidence_storage
        )
        cls.storage.cleanup()
        with psycopg.connect(cls.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.dbname)))

    def request(self, method, path, expected=200, **kwargs):
        result = self.client.request(method, "/api/v1" + path, **kwargs)
        self.assertEqual(result.status_code, expected, result.text)
        return result.json() if expected != 204 else None

    def test_unified_engine_migration_keeps_legacy_and_allows_safe_plan(self):
        migration = (ROOT / "database/009_unified_engine_selection.sql").read_text()
        node_id, admin_id = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO users(id, telegram_user_id, role) VALUES (%s, %s, 'admin')",
                         (admin_id, 99502501))
            conn.execute("""INSERT INTO proxy_nodes(id, name, host, port)
                            VALUES (%s, 'Legacy for engine test', %s, 8443)""",
                         (node_id, f"legacy-{node_id.hex[:12]}.example.org"))
            # Миграция поверх существующей БД не выбирает движок вместо оператора.
            conn.execute(migration)
            conn.execute(migration)
            legacy = conn.execute(
                "SELECT target_engine, engine_selection_mode FROM proxy_nodes WHERE id=%s",
                (node_id,)).fetchone()
            self.assertEqual(legacy, (None, "legacy"))
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": admin_id, "role": "admin"}
        try:
            result = self.client.patch(
                f"/api/v1/admin/proxy-nodes/{node_id}/engine-plan",
                json={"environment": "staging", "deployment": "native",
                      "engine_mode": "manual", "preferred_engine": "telemt"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["target_engine"], "telemt")
            self.assertFalse(result.json()["provisioning_verified"])
            with psycopg.connect(self.dsn) as conn:
                row = conn.execute("""SELECT target_engine, engine_selection_mode,
                                             deployment_profile FROM proxy_nodes
                                      WHERE id=%s""", (node_id,)).fetchone()
                self.assertEqual(row, ("telemt", "manual", "native"))
                audit = conn.execute(
                    "SELECT count(*) FROM audit_events WHERE event_type='proxy_engine_plan_updated'"
                    " AND actor_user_id=%s", (admin_id,)).fetchone()
                self.assertEqual(audit[0], 1)
        finally:
            main.app.dependency_overrides.clear()

    def test_official_docker_engine_migration_and_real_postgres(self):
        """Новый тип не ломает старые записи, config preview только текст."""
        main.app.dependency_overrides.clear()
        admin_id, node_id = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO users(id, telegram_user_id, role) VALUES (%s, 99401, 'admin')",
                (admin_id,),
            )
            conn.execute(
                """INSERT INTO proxy_nodes(id, name, host, port)
                   VALUES (%s, 'Docker Test', 'docker-test.example', 18443)""",
                (node_id,),
            )
            # Миграция не должна удалять существующие регистрации и допускает повтор.
            migration = (ROOT / "database/008_official_docker_engine.sql").read_text()
            conn.execute(migration)
            conn.execute(migration)

        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": admin_id, "role": "admin",
        }
        tag = "abcdefff00112233445566778899aabb"
        base = f"/api/v1/admin/proxy-nodes/{node_id}"
        registered = self.client.put(base + "/sponsor-registration", json={
            "engine": "official_docker", "ad_tag": tag,
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        preview = self.client.get(base + "/sponsor-config-preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["config_fragment"], "TAG=" + tag)
        self.assertFalse(preview.json()["changes_applied"])
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """SELECT engine, ad_tag FROM proxy_sponsor_registrations
                   WHERE proxy_node_id = %s""",
                (node_id,),
            ).fetchone()
            self.assertEqual(row, ("official_docker", tag))
        main.app.dependency_overrides.clear()

    def test_mtproxy_sponsor_registration_and_readiness_in_real_postgres(self):
        """Регистрация ad_tag, платёжный барьер и защита живого слота."""
        main.app.dependency_overrides.clear()
        admin_id, advertiser_id = uuid4(), uuid4()
        node_id, channel_id, campaign_id = uuid4(), uuid4(), uuid4()
        tag = "aabbccdd11223344556677889900aabb"
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """INSERT INTO users(id, telegram_user_id, role)
                   VALUES (%s, 99022, 'admin'), (%s, 99023, 'advertiser')""",
                (admin_id, advertiser_id),
            )
            connection.execute(
                """INSERT INTO proxy_nodes(id, name, host, port)
                   VALUES (%s, 'Test sponsor node', 'test-sponsor.example', 443)""",
                (node_id,),
            )
            connection.execute(
                """INSERT INTO sponsor_channels(
                       id, owner_user_id, telegram_url, telegram_channel_id, title
                   ) VALUES (%s, %s, 'https://t.me/SponsorTestChannel', -100555555,
                             'Sponsor test')""",
                (channel_id, advertiser_id),
            )
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": admin_id, "role": "admin",
        }
        admin_base = f"/api/v1/admin/proxy-nodes/{node_id}"
        registration = self.client.put(admin_base + "/sponsor-registration", json={
            "engine": "telemt", "ad_tag": tag.upper(),
        })
        self.assertEqual(registration.status_code, 200, registration.text)
        self.assertEqual(registration.json()["tag_ending"], tag[-4:])
        self.assertNotIn(tag, registration.text)
        preview = self.client.get(admin_base + "/sponsor-config-preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn(f'ad_tag = "{tag}"', preview.json()["config_fragment"])
        self.assertFalse(preview.json()["changes_applied"])

        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """INSERT INTO campaigns (
                       id, advertiser_user_id, channel_id, tariff_id, proxy_node_id,
                       tariff_name_snapshot, duration_seconds_snapshot,
                       price_kopecks_snapshot, currency_snapshot,
                       moderation_status, payment_status, placement_status,
                       scheduled_start_at, scheduled_end_at
                   ) VALUES (
                       %s, %s, %s, %s, %s, '1 день', 86400, 30000, 'RUB',
                       'approved', 'verified', 'scheduled',
                       %s, %s
                   )""",
                (campaign_id, advertiser_id, channel_id,
                 "00000000-0000-4000-8000-000000000001",
                 node_id, future, future + timedelta(days=1)),
            )
        # Нельзя незаметно поменять тег, когда место уже запланировано.
        busy = self.client.put(admin_base + "/sponsor-registration", json={
            "engine": "official_mtproxy", "ad_tag": "b" * 32,
        })
        self.assertEqual(busy.status_code, 409)

        main.app.dependency_overrides[main.require_operator] = lambda: {
            "id": admin_id, "role": "operator",
        }
        route = f"/api/v1/operator/campaigns/{campaign_id}/sponsor-readiness"
        report = self.client.get(route)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertTrue(report.json()["ready_for_manual_preparation"])
        self.assertFalse(report.json()["changes_applied"])
        self.assertEqual(report.json()["tag_ending"], tag[-4:])
        self.assertNotIn(tag, report.text)
        self.assertTrue(all(report.json()["checks"].values()))

        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "UPDATE campaigns SET payment_status = 'awaiting_payment' WHERE id = %s",
                (campaign_id,),
            )
        unpaid = self.client.get(route)
        self.assertEqual(unpaid.status_code, 200, unpaid.text)
        self.assertFalse(unpaid.json()["ready_for_manual_preparation"])
        self.assertFalse(unpaid.json()["checks"]["payment_verified"])

        with psycopg.connect(self.dsn) as connection:
            audit = connection.execute(
                """SELECT count(*) FROM audit_events
                   WHERE event_type = 'sponsor_tag_registered'
                     AND actor_user_id = %s""", (admin_id,),
            ).fetchone()[0]
            self.assertEqual(audit, 1)

        main.app.dependency_overrides.clear()

    def test_delivery_admin_diagnostics_and_retry_in_real_postgres(self):
        """Роль admin, статистика отказов, ограничение повторов и аудит."""
        main.app.dependency_overrides.clear()
        admin_id, operator_id, advertiser_id = uuid4(), uuid4(), uuid4()
        channel_id, campaign_id = uuid4(), uuid4()
        delayed, inflight, delivered, abandoned = [uuid4() for _ in range(4)]
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                INSERT INTO users(id, telegram_user_id, role)
                VALUES (%s, 89001, 'admin'), (%s, 89002, 'operator'),
                       (%s, 89003, 'advertiser')
                """,
                (admin_id, operator_id, advertiser_id),
            )
            connection.execute(
                """
                INSERT INTO sponsor_channels(id, owner_user_id, telegram_url, title)
                VALUES (%s, %s, 'https://t.me/delivery_demo', 'Demo delivery')
                """,
                (channel_id, advertiser_id),
            )
            connection.execute(
                """
                INSERT INTO campaigns (
                    id, advertiser_user_id, channel_id, tariff_id,
                    tariff_name_snapshot, duration_seconds_snapshot,
                    price_kopecks_snapshot, currency_snapshot
                ) VALUES (%s, %s, %s, %s, '1 день', 86400, 30000, 'RUB')
                """,
                (campaign_id, advertiser_id, channel_id,
                 "00000000-0000-4000-8000-000000000001"),
            )
            connection.execute(
                """
                INSERT INTO bot_advertiser_notice_outbox
                    (id, campaign_id, recipient_user_id, decision, attempts,
                     next_attempt_at, lease_until, delivered_at, abandoned_at)
                VALUES
                    (%s, %s, %s, 'approved', 3, now() + interval '20 minutes',
                     NULL, NULL, NULL),
                    (%s, %s, %s, 'rejected', 1, now() + interval '20 minutes',
                     now() + interval '60 seconds', NULL, NULL),
                    (%s, %s, %s, 'approved', 1, now(),
                     NULL, now(), NULL),
                    (%s, %s, %s, 'rejected', 4, now(),
                     NULL, NULL, now())
                """,
                (delayed, campaign_id, advertiser_id,
                 inflight, campaign_id, advertiser_id,
                 delivered, campaign_id, advertiser_id,
                 abandoned, campaign_id, advertiser_id),
            )
            connection.execute(
                """
                INSERT INTO bot_operator_notice_outbox
                    (campaign_id, operator_user_id)
                VALUES (%s, %s)
                """,
                (campaign_id, operator_id),
            )

        secret = "c" * 64
        path = "/api/v1/internal/bot/admin/"
        headers = {"X-Pushok-Bot-Secret": secret}
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": secret,
                                     "DATABASE_URL": self.dsn}):
            denied = self.client.post(path + "delivery-report",
                                      json={"telegram_user_id": 89002},
                                      headers=headers)
            self.assertEqual(denied.status_code, 403)
            report = self.client.post(path + "delivery-report",
                                      json={"telegram_user_id": 89001},
                                      headers=headers)
            self.assertEqual(report.status_code, 200, report.text)
            stats = report.json()
            # Общая БД класса уже содержит уведомления из других тестов.
            # Здесь проверяем, что наши новые состояния не пропали из отчёта.
            for state in ("waiting", "in_flight", "delivered", "abandoned"):
                self.assertGreaterEqual(stats["advertiser"][state], 1)
            self.assertGreaterEqual(stats["operator"]["waiting"], 1)
            recent_ids = {item["notice_id"] for item in stats["recent_undelivered"]}
            self.assertIn(str(delayed), recent_ids)
            self.assertIn(str(inflight), recent_ids)
            self.assertIn(str(abandoned), recent_ids)
            self.assertNotIn("telegram_user_id", report.text)

            blocked = self.client.post(path + "retry-advertiser-notice",
                                       json={"telegram_user_id": 89002,
                                             "notice_id": str(delayed)},
                                       headers=headers)
            self.assertEqual(blocked.status_code, 403)
            for ident in (inflight, delivered, abandoned):
                blocked_state = self.client.post(
                    path + "retry-advertiser-notice", headers=headers,
                    json={"telegram_user_id": 89001, "notice_id": str(ident)},
                )
                self.assertEqual(blocked_state.status_code, 409, blocked_state.text)

            requested = self.client.post(
                path + "retry-advertiser-notice", headers=headers,
                json={"telegram_user_id": 89001, "notice_id": str(delayed)},
            )
            self.assertEqual(requested.status_code, 200, requested.text)
            self.assertEqual(requested.json(), {"queued": True})
            again = self.client.post(
                path + "retry-advertiser-notice", headers=headers,
                json={"telegram_user_id": 89001, "notice_id": str(delayed)},
            )
            self.assertEqual(again.status_code, 409)

        with psycopg.connect(self.dsn) as connection:
            due = connection.execute(
                """SELECT next_attempt_at <= now()
                   FROM bot_advertiser_notice_outbox WHERE id = %s""",
                (delayed,),
            ).fetchone()[0]
            self.assertTrue(due)
            records = connection.execute(
                """SELECT count(*) FROM audit_events
                   WHERE actor_user_id = %s AND campaign_id = %s
                     AND event_type = 'advertiser_notice_retry_requested'""",
                (admin_id, campaign_id),
            ).fetchone()[0]
            self.assertEqual(records, 1)

    def test_advertiser_moderation_outbox_in_real_postgres(self):
        """Два пути модерации -> транзакционный outbox и lease/ACK."""
        main.app.dependency_overrides.clear()
        owner_id, operator_id, channel_id = uuid4(), uuid4(), uuid4()
        web_campaign, bot_campaign = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """INSERT INTO users(id, telegram_user_id, role)
                   VALUES (%s, 86001, 'advertiser'), (%s, 86002, 'operator')""",
                (owner_id, operator_id),
            )
            connection.execute(
                """INSERT INTO sponsor_channels(id, owner_user_id, telegram_url, title)
                   VALUES (%s, %s, 'https://t.me/notices_test', 'Notices')""",
                (channel_id, owner_id),
            )
            for campaign in (web_campaign, bot_campaign):
                connection.execute(
                    """INSERT INTO campaigns (
                       id, advertiser_user_id, channel_id, tariff_id,
                       tariff_name_snapshot, duration_seconds_snapshot,
                       price_kopecks_snapshot, currency_snapshot, moderation_status
                       ) VALUES (%s, %s, %s, %s, '1 день', 86400, 30000, 'RUB', 'pending')""",
                    (campaign, owner_id, channel_id,
                     "00000000-0000-4000-8000-000000000001"),
                )

        # Старый операторский веб-маршрут должен создавать такое же событие.
        main.app.dependency_overrides[main.require_operator] = lambda: {
            "id": operator_id, "role": "operator",
        }
        web = self.client.post(
            "/api/v1/operator/campaigns/" + str(web_campaign) + "/moderation",
            json={"decision": "approved"},
        )
        main.app.dependency_overrides.clear()
        self.assertEqual(web.status_code, 200, web.text)

        secret = "a" * 64
        headers = {"X-Pushok-Bot-Secret": secret}
        prefix = "/api/v1/internal/bot/"
        with patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": secret, "DATABASE_URL": self.dsn,
        }):
            bot = self.client.post(prefix + "operators/decide",
                                   headers=headers, json={
                                       "telegram_user_id": 86002,
                                       "campaign_id": str(bot_campaign),
                                       "decision": "rejected",
                                       "comment": "Исправьте запрещённое описание",
                                   })
            self.assertEqual(bot.status_code, 200, bot.text)
            with psycopg.connect(self.dsn) as conn:
                records = conn.execute(
                    """SELECT campaign_id, decision, comment_snapshot
                       FROM bot_advertiser_notice_outbox
                       WHERE campaign_id IN (%s, %s)
                       ORDER BY campaign_id""",
                    (web_campaign, bot_campaign),
                ).fetchall()
                self.assertEqual(len(records), 2)
                self.assertEqual({r[1] for r in records}, {"approved", "rejected"})
                self.assertIn("Исправьте запрещённое описание",
                              [r[2] for r in records])

            claim = self.client.post(prefix + "advertisers/outbox/claim",
                                     headers=headers)
            self.assertEqual(claim.status_code, 200, claim.text)
            batch = [r for r in claim.json()
                     if r["campaign_id"] in {str(web_campaign), str(bot_campaign)}]
            self.assertEqual(len(batch), 2)
            self.assertEqual({r["telegram_user_id"] for r in batch}, {86001})

            second = self.client.post(prefix + "advertisers/outbox/claim",
                                      headers=headers)
            self.assertFalse(any(r["campaign_id"] in
                                 {str(web_campaign), str(bot_campaign)}
                                 for r in second.json()))

            first = batch[0]
            bad = self.client.post(prefix + "advertisers/outbox/finish",
                                   headers=headers, json={
                                       "notice_id": first["notice_id"],
                                       "claim_token": str(uuid4()),
                                   })
            self.assertEqual(bad.status_code, 409)
            ack = self.client.post(prefix + "advertisers/outbox/finish",
                                   headers=headers, json={
                                       "notice_id": first["notice_id"],
                                       "claim_token": first["claim_token"],
                                   })
            self.assertEqual(ack.status_code, 200, ack.text)

            # Вторая отправка не получила ACK. Через таймаут её можно забрать
            # новым обработчиком; старый claim_token должен стать недействительным.
            unacked = batch[1]
            with psycopg.connect(self.dsn) as conn:
                conn.execute(
                    """UPDATE bot_advertiser_notice_outbox
                       SET lease_until = now() - interval '1 second',
                           next_attempt_at = now() - interval '1 second'
                       WHERE id = %s""",
                    (unacked["notice_id"],),
                )
            retried = self.client.post(prefix + "advertisers/outbox/claim",
                                       headers=headers)
            self.assertEqual(retried.status_code, 200, retried.text)
            again = next(x for x in retried.json()
                         if x["notice_id"] == unacked["notice_id"])
            self.assertNotEqual(again["claim_token"], unacked["claim_token"])
            old_ack = self.client.post(prefix + "advertisers/outbox/finish",
                                       headers=headers, json={
                                           "notice_id": unacked["notice_id"],
                                           "claim_token": unacked["claim_token"],
                                       })
            self.assertEqual(old_ack.status_code, 409)
            good_ack = self.client.post(prefix + "advertisers/outbox/finish",
                                        headers=headers, json={
                                            "notice_id": again["notice_id"],
                                            "claim_token": again["claim_token"],
                                        })
            self.assertEqual(good_ack.status_code, 200, good_ack.text)

        with psycopg.connect(self.dsn) as conn:
            counts = conn.execute(
                """SELECT count(*), count(delivered_at)
                   FROM bot_advertiser_notice_outbox
                   WHERE campaign_id IN (%s, %s)""",
                (web_campaign, bot_campaign),
            ).fetchone()
            self.assertEqual(counts, (2, 2))

    def test_bot_draft_in_real_postgres_with_retry(self):
        """В PostgreSQL один request_key создаёт ровно один черновик."""
        main.app.dependency_overrides.clear()
        secret = "d" * 64
        key = str(uuid4())
        url = "/api/v1/internal/bot/campaign-drafts"
        headers = {"X-Pushok-Bot-Secret": secret}
        payload = {
            "telegram_user_id": 70004,
            "channel_username": "TestMyChannel",
            "request_key": key,
        }
        with patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": secret, "DATABASE_URL": self.dsn,
        }):
            first = self.client.post(url, json=payload, headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["moderation_status"], "draft")
            self.assertEqual(first.json()["price_kopecks"], 30000)

            replay = self.client.post(url, json=payload, headers=headers)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()["campaign_id"], first.json()["campaign_id"])

            forbidden = self.client.post(
                url, json={**payload, "telegram_user_id": 70005},
                headers=headers,
            )
            self.assertEqual(forbidden.status_code, 409, forbidden.text)

        with psycopg.connect(self.dsn) as connection:
            count = connection.execute(
                "SELECT count(*) FROM campaigns WHERE bot_request_key = %s",
                (key,),
            ).fetchone()[0]
            self.assertEqual(count, 1)
            row = connection.execute(
                """SELECT moderation_status, payment_status,
                          price_kopecks_snapshot, currency_snapshot
                   FROM campaigns WHERE id = %s""",
                (first.json()["campaign_id"],),
            ).fetchone()
            self.assertEqual(row, ("draft", "not_required", 30000, "RUB"))
            self.assertIsNone(connection.execute(
                "SELECT id FROM users WHERE telegram_user_id = 70005"
            ).fetchone())

    def test_bot_draft_migration_is_idempotent(self):
        """Повторная миграция не удаляет данные и не рушит индекс."""
        migration = (ROOT / "database/003_bot_draft_idempotency.sql").read_text()
        with psycopg.connect(self.dsn) as connection:
            connection.execute(migration)
            connection.execute(migration)
            self.assertTrue(connection.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_indexes WHERE indexname = 'campaigns_bot_request_key_uq')"
            ).fetchone()[0])

    def test_bot_submission_in_real_postgres(self):
        """Бот не обходит владельца черновика; отправка оставляет запись в аудите."""
        main.app.dependency_overrides.clear()
        secret = "e" * 64
        channel_id = -1001234000567
        base = "/api/v1/internal/bot/"
        headers = {"X-Pushok-Bot-Secret": secret}
        new_draft = {
            "telegram_user_id": 70006,
            "channel_username": "MyChannelVerify",
            "request_key": str(uuid4()),
        }
        with patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": secret, "DATABASE_URL": self.dsn,
        }), patch("app.bot_routes.verify_channel_admin",
                  return_value=channel_id) as verifier:
            draft = self.client.post(base + "campaign-drafts",
                                     headers=headers, json=new_draft)
            self.assertEqual(draft.status_code, 200, draft.text)
            campaign_id = draft.json()["campaign_id"]
            submit = {
                "telegram_user_id": 70006, "campaign_id": campaign_id,
            }
            first = self.client.post(base + "campaign-drafts/submit",
                                     headers=headers, json=submit)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["moderation_status"], "pending")
            self.assertTrue(first.json()["verified_now"])
            verifier.assert_called_once_with("mychannelverify", 70006)

            again = self.client.post(base + "campaign-drafts/submit",
                                     headers=headers, json=submit)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertFalse(again.json()["verified_now"])
            verifier.assert_called_once()  # идемпотентный повтор без Telegram API

            foreign = self.client.post(base + "campaign-drafts/submit",
                                       headers=headers, json={
                                           **submit, "telegram_user_id": 70007,
                                       })
            self.assertEqual(foreign.status_code, 404)

        with psycopg.connect(self.dsn) as connection:
            row = connection.execute("""
                SELECT c.moderation_status, c.payment_status,
                       sc.telegram_channel_id
                FROM campaigns c JOIN sponsor_channels sc ON sc.id = c.channel_id
                WHERE c.id = %s
            """, (campaign_id,)).fetchone()
            self.assertEqual(row, ("pending", "not_required", channel_id))
            audit_count = connection.execute("""
                SELECT count(*) FROM audit_events
                WHERE campaign_id = %s AND event_type = 'campaign_submitted_from_bot'
            """, (campaign_id,)).fetchone()[0]
            self.assertEqual(audit_count, 1)

    def test_operator_bot_inbox_and_decision_in_real_postgres(self):
        """Подписка, очередь и решение проверяют роль и аудит в PostgreSQL."""
        main.app.dependency_overrides.clear()
        secret = "e" * 64
        headers = {"X-Pushok-Bot-Secret": secret}
        owner_id, op_id, channel_id, campaign_id = uuid4(), uuid4(), uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO users(id, telegram_user_id, role)
                VALUES (%s, 79002, 'advertiser'), (%s, 79001, 'operator')
                """, (owner_id, op_id),
            )
            conn.execute(
                """
                INSERT INTO sponsor_channels(id, owner_user_id, telegram_url, title)
                VALUES (%s, %s, 'https://t.me/inbox_test', 'InboxTest')
                """, (channel_id, owner_id),
            )
            conn.execute(
                """
                INSERT INTO campaigns(
                    id, advertiser_user_id, channel_id, tariff_id,
                    tariff_name_snapshot, duration_seconds_snapshot,
                    price_kopecks_snapshot, currency_snapshot,
                    moderation_status
                )
                VALUES (%s, %s, %s, %s, '1 день', 86400, 30000, 'RUB', 'pending')
                """, (
                    campaign_id, owner_id, channel_id,
                    "00000000-0000-4000-8000-000000000001",
                ),
            )

        root = "/api/v1/internal/bot/operators/"
        with patch.dict(os.environ, {"BOT_INTERNAL_SECRET": secret,
                                     "DATABASE_URL": self.dsn}):
            denied = self.client.post(root + "queue", headers=headers,
                                      json={"telegram_user_id": 79002})
            self.assertEqual(denied.status_code, 403)
            queue = self.client.post(root + "queue", headers=headers,
                                     json={"telegram_user_id": 79001, "subscribe": True})
            self.assertEqual(queue.status_code, 200, queue.text)
            self.assertTrue(queue.json()["subscribed"])
            self.assertIn(str(campaign_id),
                          [entry["campaign_id"] for entry in queue.json()["pending"]])

            users = self.client.post(root + "recipients", headers=headers)
            self.assertEqual(users.status_code, 200)
            self.assertIn(79001, users.json())

            invalid = self.client.post(root + "decide", headers=headers, json={
                "telegram_user_id": 79001,
                "campaign_id": str(campaign_id),
                "decision": "rejected",
                "comment": "",
            })
            self.assertEqual(invalid.status_code, 422)

            other = self.client.post(root + "decide", headers=headers, json={
                "telegram_user_id": 79002,
                "campaign_id": str(campaign_id), "decision": "approved",
            })
            self.assertEqual(other.status_code, 403)

            approved = self.client.post(root + "decide", headers=headers, json={
                "telegram_user_id": 79001,
                "campaign_id": str(campaign_id), "decision": "approved",
            })
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["payment_status"], "awaiting_payment")

            repeated = self.client.post(root + "decide", headers=headers, json={
                "telegram_user_id": 79001,
                "campaign_id": str(campaign_id), "decision": "approved",
            })
            self.assertEqual(repeated.status_code, 409)

            with psycopg.connect(self.dsn) as conn:
                record = conn.execute(
                    "SELECT moderation_status, payment_status FROM campaigns WHERE id = %s",
                    (campaign_id,),
                ).fetchone()
                self.assertEqual(record, ("approved", "awaiting_payment"))
                audit = conn.execute(
                    """SELECT count(*) FROM audit_events
                       WHERE campaign_id = %s AND actor_user_id = %s
                         AND event_type = 'campaign_moderated'""",
                    (campaign_id, op_id),
                ).fetchone()[0]
                self.assertEqual(audit, 1)

                # Отозванная роль сразу лишает подписчика права получать уведомления.
                conn.execute("UPDATE users SET role = 'advertiser' WHERE id = %s", (op_id,))
            revoked = self.client.post(root + "recipients", headers=headers)
            self.assertNotIn(79001, revoked.json())

            stopped = self.client.post(root + "unsubscribe", headers=headers,
                                       json={"telegram_user_id": 79001})
            self.assertEqual(stopped.status_code, 200)
            with psycopg.connect(self.dsn) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM bot_operator_subscriptions WHERE operator_user_id = %s",
                    (op_id,),
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_operator_outbox_survives_retries_and_role_revocation(self):
        """Новая pending-заявка надёжно сохраняет notice до успешного ACK."""
        main.app.dependency_overrides.clear()
        base = "/api/v1/internal/bot/"
        secret = "e" * 64
        headers = {"X-Pushok-Bot-Secret": secret}
        operator_id, advertiser_id = 73001, 73002
        key = str(uuid4())
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "INSERT INTO users (telegram_user_id, role) VALUES (%s, 'admin')",
                (operator_id,),
            )
        with patch.dict(os.environ, {
            "BOT_INTERNAL_SECRET": secret, "DATABASE_URL": self.dsn,
        }), patch("app.bot_routes.verify_channel_admin",
                  return_value=-1001234555000):
            subscribe = self.client.post(base + "operators/queue",
                                         headers=headers, json={
                                             "telegram_user_id": operator_id,
                                             "subscribe": True,
                                         })
            self.assertEqual(subscribe.status_code, 200, subscribe.text)
            self.assertTrue(subscribe.json()["subscribed"])

            draft = self.client.post(base + "campaign-drafts", headers=headers, json={
                "telegram_user_id": advertiser_id,
                "channel_username": "OutboxTestChannel",
                "request_key": key,
            })
            self.assertEqual(draft.status_code, 200, draft.text)
            campaign_id = draft.json()["campaign_id"]
            submit = self.client.post(base + "campaign-drafts/submit",
                                      headers=headers, json={
                                          "telegram_user_id": advertiser_id,
                                          "campaign_id": campaign_id,
                                      })
            self.assertEqual(submit.status_code, 200, submit.text)
            self.assertTrue(submit.json()["verified_now"])

            # Отозванная роль немедленно блокирует выдачу отложенного сообщения.
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    "UPDATE users SET role = 'advertiser' WHERE telegram_user_id = %s",
                    (operator_id,),
                )
            no_access = self.client.post(base + "operators/outbox/claim", headers=headers)
            self.assertEqual(no_access.status_code, 200, no_access.text)
            self.assertEqual(no_access.json(), [])

            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    "UPDATE users SET role = 'admin' WHERE telegram_user_id = %s",
                    (operator_id,),
                )
            first = self.client.post(base + "operators/outbox/claim", headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            matches = [notice for notice in first.json()
                       if notice["campaign_id"] == campaign_id]
            self.assertEqual(len(matches), 1)
            notice_id = matches[0]["notice_id"]
            self.assertEqual(matches[0]["telegram_user_id"], operator_id)

            # Пока таймаут не истёк, сообщение другим воркером не забирается.
            immediate = self.client.post(base + "operators/outbox/claim",
                                         headers=headers)
            self.assertEqual(immediate.status_code, 200)
            self.assertNotIn(notice_id, [x["notice_id"] for x in immediate.json()])

            # Имитируем падение после claim и до Telegram/ACK.
            with psycopg.connect(self.dsn) as connection:
                connection.execute(
                    """UPDATE bot_operator_notice_outbox
                       SET last_attempt_at = now() - interval '3 minutes'
                       WHERE id = %s""",
                    (notice_id,),
                )
            retry = self.client.post(base + "operators/outbox/claim",
                                     headers=headers)
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertIn(notice_id, [x["notice_id"] for x in retry.json()])

            ack = self.client.post(base + "operators/outbox/ack",
                                   headers=headers, json={"notice_id": notice_id})
            self.assertEqual(ack.status_code, 200, ack.text)
            ack_again = self.client.post(base + "operators/outbox/ack",
                                         headers=headers, json={"notice_id": notice_id})
            self.assertEqual(ack_again.status_code, 200, ack_again.text)
            finished = self.client.post(base + "operators/outbox/claim",
                                        headers=headers)
            self.assertNotIn(notice_id, [x["notice_id"] for x in finished.json()])

        with psycopg.connect(self.dsn) as connection:
            row = connection.execute(
                """SELECT attempts, delivered_at IS NOT NULL
                   FROM bot_operator_notice_outbox WHERE id = %s""",
                (notice_id,),
            ).fetchone()
            self.assertEqual(row, (2, True))
            audit_count = connection.execute(
                """SELECT count(*) FROM audit_events
                   WHERE campaign_id = %s AND event_type = 'campaign_submitted_from_bot'""",
                (campaign_id,),
            ).fetchone()[0]
            self.assertEqual(audit_count, 1)

    def test_outbox_migration_idempotent(self):
        migration = (ROOT / "database/005_operator_notice_outbox.sql").read_text()
        with psycopg.connect(self.dsn) as connection:
            connection.execute(migration)
            connection.execute(migration)
            self.assertTrue(connection.execute(
                "SELECT to_regclass('public.bot_operator_notice_outbox') IS NOT NULL"
            ).fetchone()[0])

    def test_campaign_lifecycle_and_slot_collision(self):
        owner, operator = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO users(id, telegram_user_id, role) VALUES (%s, 70001, 'advertiser'), (%s, 70002, 'admin')", (owner, operator))
        actor = {"id": owner, "role": "advertiser"}
        main.app.dependency_overrides[main.get_current_user] = lambda: actor
        channel = self.request("POST", "/channels", 201, json={"title": "Тест", "telegram_url": "https://t.me/pushok_test"})
        tariff = self.request("GET", "/tariffs")[0]
        desired = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
        campaign = self.request("POST", "/campaigns", 201, json={"channel_id": channel["id"], "tariff_id": tariff["id"], "requested_start_at": desired.isoformat()})
        self.assertEqual(datetime.fromisoformat(campaign["requested_start_at"].replace("Z", "+00:00")), desired)
        cid = campaign["id"]
        self.request("POST", "/campaigns/" + cid + "/submit")
        actor = {"id": operator, "role": "admin"}
        self.request("POST", "/operator/campaigns/" + cid + "/moderation", json={"decision": "changes_requested", "comment": "Исправьте название"})
        actor = {"id": owner, "role": "advertiser"}
        self.request("PATCH", "/campaigns/" + cid + "/channel", json={"title": "Исправлено", "telegram_url": "https://t.me/pushok_test"})
        self.request("POST", "/campaigns/" + cid + "/submit")
        actor = {"id": operator, "role": "admin"}
        self.request("POST", "/operator/campaigns/" + cid + "/moderation", json={"decision": "approved"})
        actor = {"id": owner, "role": "advertiser"}
        os.environ["PAYMENT_INSTRUCTIONS_B64"] = base64.b64encode("Тестовые реквизиты".encode()).decode()
        instructions = self.request("GET", "/campaigns/" + cid + "/payment-instructions")
        self.assertEqual(instructions["amount_kopecks"], 30000)
        proof = self.request("POST", "/campaigns/" + cid + "/payment-proofs", 201, files={"file": ("receipt.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
        download = "/operator/campaigns/" + cid + "/payment-proofs/" + proof["id"]
        self.request("GET", download, 403)
        actor = {"id": operator, "role": "admin"}
        self.assertEqual(self.client.get("/api/v1" + download).status_code, 200)
        self.request("POST", "/operator/campaigns/" + cid + "/payment-review", json={"decision": "verified"})
        node = self.request("POST", "/admin/proxy-nodes", 201, json={"name": "Тестовый узел", "host": "proxy.example.org", "port": 8443})
        start = desired
        params = {"tariff_id": tariff["id"], "date_from": start.date().isoformat(), "start_time": start.strftime("%H:%M:%S"), "timezone_name": "UTC", "days": 2}
        before = self.request("GET", "/availability", params=params)
        self.assertEqual(before["days"][0]["available_nodes"], 1)
        self.assertTrue(before["reservation"])
        self.assertEqual(before["hold_ttl_seconds"], 900)
        self.assertEqual(set(before["days"][0]), {"date", "start_at", "end_at", "available_nodes", "status"})
        scheduled = self.request("POST", "/operator/campaigns/" + cid + "/schedule", json={"proxy_node_id": node["id"], "start_at": start.isoformat()})
        self.assertEqual(scheduled["placement_status"], "scheduled")
        busy = self.request("GET", "/availability", params=params)
        self.assertEqual(busy["days"][0]["available_nodes"], 0)
        self.assertEqual(busy["days"][1]["available_nodes"], 1)
        self.request("PATCH", "/admin/proxy-nodes/" + node["id"] + "/status", 409, json={"status": "paused"})
        self.request("POST", "/operator/campaigns/" + cid + "/activate", 409, json={"tag_applied_on_node": True, "mtproxybot_promotion_selected": True,
                       "independent_client_checked": True,
                       "verification_note": "Проверка со второго клиента Telegram, канал показан вручную."})
        # Вторая оплаченная кампания нужна для проверки ограничения интервалов в PostgreSQL.
        with psycopg.connect(self.dsn) as conn:
            second = conn.execute("""
                INSERT INTO campaigns(advertiser_user_id, channel_id, tariff_id,
                  tariff_name_snapshot, duration_seconds_snapshot, price_kopecks_snapshot,
                  currency_snapshot, moderation_status, payment_status)
                SELECT advertiser_user_id, channel_id, tariff_id, tariff_name_snapshot,
                  duration_seconds_snapshot, price_kopecks_snapshot, currency_snapshot, 'approved', 'verified'
                FROM campaigns WHERE id = %s RETURNING id
            """, (cid,)).fetchone()[0]
        self.request("POST", "/operator/campaigns/" + str(second) + "/schedule", 409, json={"proxy_node_id": node["id"], "start_at": start.isoformat()})
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE campaigns SET scheduled_start_at=now()-interval '1 minute', scheduled_end_at=now()+interval '1 hour' WHERE id=%s", (cid,))
        # Активация не может полагаться только на нажатие кнопки оператора.
        self.request("POST", "/operator/campaigns/" + cid + "/activate", 409, json={"tag_applied_on_node": True, "mtproxybot_promotion_selected": True,
                       "independent_client_checked": True,
                       "verification_note": "Проверка со второго клиента Telegram, канал показан вручную."})
        observation_url = "/operator/campaigns/" + cid + "/display-observations"
        negative = self.request("POST", observation_url, 201, json={
            "result": "not_visible", "method": "separate_unsubscribed_client",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "note": "Показ в отдельном клиенте не обнаружен",
        })
        self.assertEqual(negative["result"], "not_visible")
        self.request("POST", "/operator/campaigns/" + cid + "/activate", 409, json={"tag_applied_on_node": True, "mtproxybot_promotion_selected": True,
                       "independent_client_checked": True,
                       "verification_note": "Проверка со второго клиента Telegram, канал показан вручную."})
        positive = self.request("POST", observation_url, 201, json={
            "result": "visible", "method": "separate_unsubscribed_client",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "note": "Показ вручную виден во втором аккаунте",
        })
        self.assertEqual(positive["result"], "visible")
        reports = self.request("GET", observation_url)
        self.assertEqual([r["result"] for r in reports[:2]], ["visible", "not_visible"])
        actor = {"id": owner, "role": "advertiser"}
        public_report = self.request("GET", "/campaigns/" + cid + "/display-observation")
        self.assertEqual(public_report["result"], "visible")
        self.assertNotIn("note", public_report)
        actor = {"id": operator, "role": "admin"}
        self.assertEqual(self.request("POST", "/operator/campaigns/" + cid + "/activate", json={"tag_applied_on_node": True, "mtproxybot_promotion_selected": True,
                       "independent_client_checked": True,
                       "verification_note": "Проверка со второго клиента Telegram, канал показан вручную."})["placement_status"], "active")
        self.request("POST", "/operator/campaigns/" + cid + "/complete", 422)
        self.request("POST", "/operator/campaigns/" + cid + "/complete", 422,
                     json={"tag_removed_confirmed": False,
                           "verification_method": "separate_unsubscribed_client"})
        self.assertEqual(self.request("POST", "/operator/campaigns/" + cid + "/complete",
                         json={"tag_removed_confirmed": True,
                               "verification_method": "separate_unsubscribed_client"})["placement_status"], "completed")
        self.request("POST", "/operator/campaigns/" + cid + "/complete", 409,
                     json={"tag_removed_confirmed": True,
                           "verification_method": "separate_unsubscribed_client"})
        with psycopg.connect(self.dsn) as conn:
            completed_event = conn.execute(
                """SELECT details_json FROM audit_events
                   WHERE campaign_id=%s AND event_type='placement_completed_manually'""",
                (cid,),
            ).fetchone()[0]
        self.assertIs(completed_event["tag_removed_confirmed"], True)
        self.assertEqual(completed_event["verification_method"], "separate_unsubscribed_client")
        self.request("PATCH", "/admin/proxy-nodes/" + node["id"] + "/status", json={"status": "paused"})
        with psycopg.connect(self.dsn) as conn:
            self.assertGreaterEqual(conn.execute("SELECT count(*) FROM audit_events WHERE campaign_id=%s", (cid,)).fetchone()[0], 8)
        main.app.dependency_overrides.clear()

    def test_signed_login_session_and_logout(self):
        main.app.dependency_overrides.clear()
        old_token, old_key = main.TELEGRAM_BOT_TOKEN, main.SESSION_SIGNING_KEY
        main.TELEGRAM_BOT_TOKEN, main.SESSION_SIGNING_KEY = "123456:integration_test_only", "x" * 64
        try:
            data = {"id": 70003, "first_name": "Тест", "auth_date": int(time.time())}
            check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
            data["hash"] = hmac.new(hashlib.sha256(main.TELEGRAM_BOT_TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
            session = self.request("POST", "/auth/telegram", json=data)
            headers = {"Authorization": "Bearer " + session["access_token"]}
            self.assertEqual(self.request("GET", "/me", headers=headers)["telegram_user_id"], 70003)
            self.request("POST", "/auth/telegram", 401, json=data)
            self.request("POST", "/auth/logout", 204, headers=headers)
            self.request("GET", "/me", 401, headers=headers)
        finally:
            main.TELEGRAM_BOT_TOKEN, main.SESSION_SIGNING_KEY = old_token, old_key
