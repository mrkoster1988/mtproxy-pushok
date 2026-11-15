"""v0.048 — администратор меняет тариф, старые заказы не пересчитываются."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main

ADMIN = UUID("00000000-0000-4000-8000-000000000993")
TARIFF = UUID("00000000-0000-4000-8000-000000000001")
PATH = f"/api/v1/admin/tariffs/{TARIFF}"
VALID = {"name": "1 день", "duration_seconds": 86400, "price_kopecks": 30000,
         "stars_price_xtr": None, "is_active": True}


class TariffSettingsTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def as_admin(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {
            "id": ADMIN, "role": "admin"
        }

    def test_unauthenticated_cannot_read_or_write(self):
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.client.get("/api/v1/admin/tariffs").status_code, 401)
            self.assertEqual(self.client.put(PATH, json=VALID).status_code, 401)
            self.assertEqual(self.client.post("/api/v1/admin/tariffs", json={**VALID,"request_id":str(uuid4())}).status_code, 401)
        db.assert_not_called()

    def test_operator_is_not_admin(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": ADMIN, "role": "operator"
        }
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.client.get("/api/v1/admin/tariffs").status_code, 403)
            self.assertEqual(self.client.put(PATH, json=VALID).status_code, 403)
            self.assertEqual(self.client.post("/api/v1/admin/tariffs", json={**VALID,"request_id":str(uuid4())}).status_code, 403)
        db.assert_not_called()

    def test_invalid_price_and_stars_rejected_before_db(self):
        self.as_admin()
        invalid = [
            {"price_kopecks": -300}, {"price_kopecks": 1}, {"price_kopecks": 99},
            {"stars_price_xtr": -3},
            {"duration_seconds": 0}, {"name": "    "},
            {"is_active": "maybe"},
            {"admin_only": False},
        ]
        with patch("app.main.psycopg.connect") as db:
            for update in invalid:
                with self.subTest(update=update):
                    response = self.client.put(PATH, json={**VALID, **update})
                    self.assertEqual(response.status_code, 422, response.text)
        db.assert_not_called()

    def test_update_is_atomic_and_audited_without_touching_campaigns(self):
        self.as_admin()
        record = {
            "id": TARIFF, "name": "2 дня", "duration_seconds": 172800,
            "price_kopecks": 45000, "stars_price_xtr": 200,
            "currency": "RUB ", "is_active": True
        }
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = record
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.put(PATH, json={
                "name": "2 дня", "duration_seconds": 172800, "price_kopecks": 45000,
                "stars_price_xtr": 200, "is_active": True
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stars_price_xtr"], 200)
        self.assertEqual(response.json()["price_kopecks"], 45000)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 2)
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertIn("UPDATE tariffs", calls[0].args[0])
        self.assertNotIn("campaigns", str(calls))
        self.assertIn("tariff_settings_updated", calls[1].args[0])

    def test_create_free_tariff_audits_once_and_normalizes_zero_stars(self):
        self.as_admin()
        request_id=uuid4()
        body={**VALID,'request_id':str(request_id),'name':'  Бесплатный   день  ',
              'price_kopecks':0,'stars_price_xtr':0}
        record={**VALID,'id':request_id,'name':'Бесплатный день','price_kopecks':0,
                'stars_price_xtr':None,'admin_only':False,'currency':'RUB '}
        conn=MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value=record
        with patch('app.main.psycopg.connect',return_value=conn):
            response=self.client.post('/api/v1/admin/tariffs',json=body)
        self.assertEqual(response.status_code,201,response.text)
        self.assertIsNone(response.json()['stars_price_xtr'])
        self.assertEqual(response.json()['name'],'Бесплатный день')
        calls=conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls),2)
        self.assertIn('INSERT INTO tariffs',calls[0].args[0])
        self.assertIn('tariff_created',calls[1].args[0])
        self.assertNotIn('campaigns',str(calls))

    def test_create_validation_and_access_fail_before_db(self):
        self.as_admin()
        body={**VALID,'request_id':str(uuid4())}
        with patch('app.main.psycopg.connect') as db:
            for changes in ({'request_id':'bad'},{'name':'   '},{'price_kopecks':-1},
                            {'price_kopecks':99},{'price_kopecks':0,'stars_price_xtr':1},
                            {'admin_only':True},{'duration_seconds':3599},
                            {'duration_seconds':31536001},{'currency':'USD'}):
                response=self.client.post('/api/v1/admin/tariffs',json={**body,**changes})
                self.assertEqual(response.status_code,422,response.text)
        db.assert_not_called()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides[main.get_current_user]=lambda:{'id':ADMIN,'role':'advertiser'}
        with patch('app.main.psycopg.connect') as db:
            self.assertEqual(self.client.post('/api/v1/admin/tariffs',json=body).status_code,403)
        db.assert_not_called()

    def test_creation_retry_returns_existing_and_rejects_changed_payload(self):
        self.as_admin()
        request_id=uuid4()
        body={**VALID,'request_id':str(request_id)}
        record={**VALID,'id':request_id,'admin_only':False,'currency':'RUB'}
        for old,code in ((record,200),({**record,'price_kopecks':0},409),(None,409)):
            conn=MagicMock()
            conn.__enter__.return_value.execute.return_value.fetchone.side_effect=[None,old]
            with patch('app.main.psycopg.connect',return_value=conn):
                response=self.client.post('/api/v1/admin/tariffs',json=body)
            self.assertEqual(response.status_code,code,response.text)
            self.assertEqual(conn.__enter__.return_value.execute.call_count,2)
            self.assertNotIn('tariff_created',str(conn.__enter__.return_value.execute.call_args_list))

    def test_missing_tariff_is_404_and_does_not_audit(self):
        self.as_admin()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with patch("app.main.psycopg.connect", return_value=conn):
            response = self.client.put(PATH, json=VALID)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(conn.__enter__.return_value.execute.call_count, 1)

    def test_migration_and_cabinet_wiring(self):
        sql = (ROOT / "database/014_flexible_tariff_prices.sql").read_text()
        js = (ROOT / "api/web/app.js").read_text()
        html = (ROOT / "api/web/index.html").read_text()
        bot = (ROOT / "api/app/bot_routes.py").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS stars_price_xtr", sql)
        self.assertIn("tariff-admin-form", html)
        self.assertIn("renderTariffAdmin", js)
        self.assertIn("/admin/tariffs/", js)
        self.assertNotIn("price_kopecks = 30000", bot)

    def test_operator_notification_does_not_hardcode_current_price(self):
        bot = (ROOT / "bot/main.py").read_text()
        self.assertIn("Цена и срок — в карточке заявки", bot)
        self.assertNotIn("Тариф: 1 день — 300 ₽", bot)


if __name__ == "__main__":
    unittest.main()
