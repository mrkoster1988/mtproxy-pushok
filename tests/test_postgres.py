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

    def test_campaign_lifecycle_and_slot_collision(self):
        owner, operator = uuid4(), uuid4()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("INSERT INTO users(id, telegram_user_id, role) VALUES (%s, 70001, 'advertiser'), (%s, 70002, 'admin')", (owner, operator))
        actor = {"id": owner, "role": "advertiser"}
        main.app.dependency_overrides[main.get_current_user] = lambda: actor
        channel = self.request("POST", "/channels", 201, json={"title": "Тест", "telegram_url": "https://t.me/pushok_test"})
        tariff = self.request("GET", "/tariffs")[0]
        campaign = self.request("POST", "/campaigns", 201, json={"channel_id": channel["id"], "tariff_id": tariff["id"]})
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
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        scheduled = self.request("POST", "/operator/campaigns/" + cid + "/schedule", json={"proxy_node_id": node["id"], "start_at": start.isoformat()})
        self.assertEqual(scheduled["placement_status"], "scheduled")
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
