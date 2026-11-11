"""Предохранители фоновой сверки без сети и БД."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import sbp_worker


class SBPWorkerTests(unittest.TestCase):
    def test_apply_gate_precedes_database_and_network(self):
        with patch.dict(os.environ, {'PUSHOK_SBP_RECONCILIATION_ENABLED': '0'}), \
                patch.object(sbp_worker.psycopg, 'connect') as db, \
                patch.object(sbp_worker, 'verify_background') as verify:
            self.assertEqual(sbp_worker.service_once('unused', apply=True)['status'], 'disabled')
            db.assert_not_called()
            verify.assert_not_called()

    def test_batch_limit_is_bounded_before_database(self):
        with patch.object(sbp_worker.psycopg, 'connect') as db:
            for limit in (0, -1, 101):
                with self.assertRaises(ValueError):
                    sbp_worker.service_once('unused', limit=limit)
            db.assert_not_called()

    def test_backoff_is_bounded_and_increases(self):
        self.assertEqual([sbp_worker.retry_seconds(i) for i in (1, 2, 3, 7, 1000)],
                         [60, 120, 240, 3600, 3600])
