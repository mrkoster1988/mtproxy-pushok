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


@unittest.skipUnless(os.getenv("TEST_ADMIN_DSN"), "Нужен отдельный тестовый PostgreSQL: TEST_ADMIN_DSN")
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
        cls.old_dsn, cls.old_storage = main.DATABASE_URL, main.PAYMENT_PROOFS_DIR
        cls.storage = tempfile.TemporaryDirectory()
        main.DATABASE_URL = cls.dsn
        main.PAYMENT_PROOFS_DIR = cls.storage.name
        with psycopg.connect(cls.dsn) as conn:
            for migration in sorted((ROOT / "database").glob("*.sql")):
                conn.execute(migration.read_text())
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        main.app.dependency_overrides.clear()
        main.DATABASE_URL, main.PAYMENT_PROOFS_DIR = cls.old_dsn, cls.old_storage
        cls.storage.cleanup()
        with psycopg.connect(cls.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.dbname)))

    def request(self, method, path, expected=200, **kwargs):
        result = self.client.request(method, "/api/v1" + path, **kwargs)
        self.assertEqual(result.status_code, expected, result.text)
        return result.json() if expected != 204 else None

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
        self.assertFalse(before["reservation"])
        self.assertEqual(set(before["days"][0]), {"date", "start_at", "end_at", "available_nodes"})
        scheduled = self.request("POST", "/operator/campaigns/" + cid + "/schedule", json={"proxy_node_id": node["id"], "start_at": start.isoformat()})
        self.assertEqual(scheduled["placement_status"], "scheduled")
        busy = self.request("GET", "/availability", params=params)
        self.assertEqual(busy["days"][0]["available_nodes"], 0)
        self.assertEqual(busy["days"][1]["available_nodes"], 1)
        self.request("PATCH", "/admin/proxy-nodes/" + node["id"] + "/status", 409, json={"status": "paused"})
        self.request("POST", "/operator/campaigns/" + cid + "/activate", 409)
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
        self.assertEqual(self.request("POST", "/operator/campaigns/" + cid + "/activate")["placement_status"], "active")
        self.assertEqual(self.request("POST", "/operator/campaigns/" + cid + "/complete")["placement_status"], "completed")
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
