"""Границы доступа к очереди СБП и отсутствие ручного зачёта."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from app import main, sbp_admin

BASE = '/api/v1/admin/payments/sbp/attempts'


class SBPAdminTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.addCleanup(main.app.dependency_overrides.clear)

    def test_anonymous_advertiser_operator_cannot_touch_queue(self):
        with patch.object(sbp_admin, 'list_attempts') as listing, patch.object(sbp_admin, 'request_recheck') as retry:
            for role, expected in [(None, 401), ('advertiser', 403), ('operator', 403)]:
                if role:
                    main.app.dependency_overrides[main.get_current_user] = lambda: {'id': uuid4(), 'role': role}
                self.assertEqual(self.client.get(BASE).status_code, expected)
                self.assertEqual(self.client.post(BASE+'/'+str(uuid4())+'/retry', json={'revision': 0, 'note': 'Проверили настройки'}).status_code, expected)
            listing.assert_not_called()
            retry.assert_not_called()

    def test_admin_private_listing_and_bounded_pagination(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {'id': uuid4(), 'role': 'admin'}
        with patch.object(sbp_admin, 'list_attempts', return_value={'items': [], 'has_more': False}) as listing:
            response = self.client.get(BASE)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'private, no-store')
            for suffix in ('?limit=101', '?offset=-1', '?limit=0'):
                self.assertEqual(self.client.get(BASE+suffix).status_code, 422)
            self.assertEqual(listing.call_count, 1)

    def test_request_rejects_manual_amount_status_and_short_note(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {'id': uuid4(), 'role': 'admin'}
        with patch.object(sbp_admin, 'request_recheck') as retry:
            for body in ({'revision': 0, 'note': 'short'},
                         {'revision': 0, 'note': 'Проверили настройки', 'status': 'succeeded'},
                         {'revision': 0, 'note': 'Проверили настройки', 'amount': 1}):
                self.assertEqual(self.client.post(BASE+'/'+str(uuid4())+'/retry', json=body).status_code, 422)
            retry.assert_not_called()

    def test_disabled_retry_does_not_touch_database(self):
        with patch.dict(os.environ, {'PUSHOK_SBP_RECONCILIATION_ENABLED': '0'}), patch.object(sbp_admin.psycopg, 'connect') as db:
            with self.assertRaises(main.HTTPException) as error:
                sbp_admin.request_recheck('unused', uuid4(), uuid4(), 0, 'Проверили настройки')
            self.assertEqual(error.exception.detail, 'sbp_reconciliation_disabled')
            db.assert_not_called()
