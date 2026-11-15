"""Диагностика не выдаёт план за запуск и не раскрывает содержимое отчёта."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
os.environ.setdefault('SESSION_SIGNING_KEY', 'a'*64)
from fastapi.testclient import TestClient
from app import main, promotion_diagnostics as diag
import promotion_agent as agent


class PromotionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / 'promotion-agent.json'
        self.env = patch.dict(os.environ, {'PUSHOK_CONTROL_DIR': self.tmp.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.now = datetime.now(timezone.utc)
        main.app.dependency_overrides.clear()
        self.addCleanup(main.app.dependency_overrides.clear)

    def report(self, code='tag_applied', **extra):
        data = dict(code=code, checked_at=self.now.isoformat(), **extra)
        self.path.write_text(json.dumps(data))

    def test_fresh_stale_future_and_absent_are_distinct(self):
        self.assertEqual(diag.agent_report(self.now)['status'], 'unknown')
        self.report()
        self.assertTrue(diag.agent_report(self.now)['recent'])
        stale = diag.agent_report(self.now + timedelta(seconds=120))
        self.assertEqual(stale['status'], 'stale')
        self.assertFalse(stale['recent'])
        self.assertEqual(stale['title'], 'Отчёт агента устарел')
        self.assertEqual(diag.agent_report(self.now - timedelta(seconds=1))['status'], 'unknown')

    def test_report_never_forwards_secret_fields_or_unknown_error_text(self):
        secret = 'SECRET-must-not-leak'
        self.report(ad_tag=secret, secret=secret, stderr=secret, host='proxy.example.org', port=8443)
        result = diag.agent_report(self.now)
        self.assertEqual(result['host'], 'proxy.example.org')
        self.assertNotIn(secret, json.dumps(result))
        self.report(code=secret)
        self.assertNotIn(secret, json.dumps(diag.agent_report(self.now)))

    def test_malformed_oversized_and_symlink_report_are_unknown(self):
        for text in ['[]', 'not-json', 'x'*9000, json.dumps({'code': [], 'checked_at': ''})]:
            self.path.write_text(text)
            self.assertEqual(diag.agent_report(self.now)['status'], 'unknown')
        self.path.unlink()
        target = self.directory / 'other.json'
        target.write_text(json.dumps(dict(code='tag_applied', checked_at=self.now.isoformat())))
        self.path.symlink_to(target)
        self.assertEqual(diag.agent_report(self.now)['status'], 'unknown')

    def test_ready_plan_does_not_claim_an_agent_or_show_a_tag(self):
        node = dict(id=uuid4(), name='Node', host='proxy.example.org', port=8443)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [node]
        with patch.object(diag, 'node_plan', return_value=dict(reason='ready', tag='SECRET', state='apply')):
            result = diag.diagnostics(conn)
        self.assertEqual(result['nodes'][0]['code'], 'ready')
        self.assertEqual(result['agent']['status'], 'unknown')
        self.assertFalse(result['display_verified'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_endpoint_rejects_non_admin_before_database_or_report_read(self):
        with TestClient(main.app) as client, patch.object(main, 'promotion_diagnostics') as read, patch.object(main.psycopg, 'connect') as db:
            self.assertEqual(client.get('/api/v1/admin/promotion-diagnostics').status_code, 401)
            for role in ['advertiser', 'operator']:
                main.app.dependency_overrides[main.get_current_user] = lambda: dict(id=uuid4(), role=role)
                self.assertEqual(client.get('/api/v1/admin/promotion-diagnostics').status_code, 403)
            read.assert_not_called(); db.assert_not_called()

    def test_database_error_has_no_connection_details(self):
        main.app.dependency_overrides[main.require_admin] = lambda: dict(id=uuid4(), role='admin')
        with TestClient(main.app) as client, patch.object(main.psycopg, 'connect', side_effect=main.psycopg.OperationalError('SECRET')):
            response = client.get('/api/v1/admin/promotion-diagnostics')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET', response.text)

    def test_failed_cycle_reports_stage_without_exception_contents(self):
        def fail(status):
            status.update(code='apply_failed', host='proxy.example.org', port=8443, tag='SECRET')
            raise RuntimeError('SECRET')
        with patch.object(agent, 'STATUS', self.path), patch.object(agent.os, 'fchown'), patch.object(agent, 'main', side_effect=fail):
            with self.assertRaises(RuntimeError):
                agent.run_cycle()
        data = json.loads(self.path.read_text())
        self.assertEqual(data['code'], 'apply_failed')
        self.assertNotIn('SECRET', self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(diag.agent_report()['code'], 'apply_failed')

    def test_reporting_failure_cannot_break_operational_cycle(self):
        with patch.object(agent, 'STATUS', self.directory/'missing'/'report.json'), patch.object(agent, 'main') as main_cycle:
            agent.run_cycle()
        main_cycle.assert_called_once()

    def test_all_agent_codes_have_fixed_explanations(self):
        self.assertEqual(agent.REPORT_CODES, set(diag.MESSAGES))
