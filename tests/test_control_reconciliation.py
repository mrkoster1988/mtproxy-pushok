"""Настройки кабинета управляют сверкой без перезапуска worker; старые счета не теряются."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
os.environ.setdefault('SESSION_SIGNING_KEY', 'a' * 64)
from fastapi.testclient import TestClient
from app import control, main, payment_gate, sbp_worker


class ReconciliationControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            'PUSHOK_CONTROL_DIR': self.directory.name,
            'PUSHOK_SBP_RECONCILIATION_ENABLED': '0',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        main.app.dependency_overrides[main.require_admin] = lambda: {
            'id': UUID('00000000-0000-4000-8000-000000000997'), 'role': 'admin',
        }
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def save(self, enabled):
        with patch.object(main.sbp_checkout, 'settings', return_value={'configured': True}):
            response = self.client.put('/api/v1/admin/control/payments', json={'stars': False, 'sbp': enabled})
        self.assertEqual(response.status_code, 200, response.text)

    def test_enabling_sbp_starts_worker_and_disabling_keeps_pending_checks(self):
        self.assertFalse(payment_gate.sbp_reconciliation_enabled())
        self.save(True)
        self.assertTrue(payment_gate.sbp_reconciliation_enabled())
        self.save(False)
        self.assertFalse(control.flag('sbp', True))
        self.assertTrue(payment_gate.sbp_reconciliation_enabled())
        # Worker reads the saved switch on each batch, even with environment = 0.
        with patch.object(sbp_worker.psycopg, 'connect', side_effect=RuntimeError('database reached')):
            with self.assertRaisesRegex(RuntimeError, 'database reached'):
                sbp_worker.service_once('unused', apply=True)

    def test_saving_disabled_new_install_does_not_start_worker(self):
        self.save(False)
        with patch.object(sbp_worker.psycopg, 'connect') as db:
            self.assertEqual(sbp_worker.service_once('unused', apply=True)['status'], 'disabled')
            db.assert_not_called()

    def test_environment_reconciliation_is_preserved_for_existing_install(self):
        with patch.dict(os.environ, {'PUSHOK_SBP_RECONCILIATION_ENABLED': '1'}):
            self.save(False)
        self.assertTrue(payment_gate.sbp_reconciliation_enabled())

    def test_bad_control_shape_and_string_flag_do_not_enable_payments(self):
        for value in ([], True, 'enabled', {'sbp': 'false'}):
            control.write('payments.json', value)
            self.assertFalse(control.flag('sbp', True))
