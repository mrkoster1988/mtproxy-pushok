"""Поведенческие проверки: некорректный конфиг закрывает запуск проверки Compose.

Все ключи искусственные. Docker, Telegram, БД и production .env не используются.
"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('staging_preflight', ROOT / 'scripts/staging_preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def fixture():
    return {
        'POSTGRES_DB': 'mtproxy_pushok', 'POSTGRES_USER': 'mtproxy_pushok',
        'POSTGRES_PASSWORD': 'a' * 64, 'SESSION_SIGNING_KEY': 'b' * 64,
        'BOT_INTERNAL_SECRET': 'c' * 64,
        'DATABASE_URL': 'postgresql://mtproxy_pushok:' + 'a' * 64 + '@db:5432/mtproxy_pushok',
        'TELEGRAM_BOT_TOKEN': '123456789:TEST_ONLY_secret_123456789',
        'TELEGRAM_BOT_USERNAME': 'mtproxy_pushok_bot',
    }


def compose(values):
    return {'services': {name: {'environment': dict(values), **(
        {'ports': [{'host_ip': '127.0.0.1', 'target': 8000, 'published': '8000'}]}
        if name == 'api' else {})} for name in ('db', 'api', 'bot', 'sbp-worker')}}


class StagingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = fixture()
        self.write_env()

    def write_env(self):
        path = self.root / '.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in self.values.items()))
        path.chmod(0o600)

    def run_check(self, *, config=None, git_code=0, git_output=b'', docker_error=None, config_only=False):
        calls = []
        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if cmd[0] == 'git':
                return subprocess.CompletedProcess(cmd, git_code, git_output, b'')
            if docker_error:
                raise docker_error
            payload = compose(self.values) if config is None else config
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload).encode(), b'')
        with patch.object(preflight.subprocess, 'run', side_effect=run):
            report = preflight.inspect(self.root, config_only=config_only)
        return report, calls

    def failed(self, report):
        return {x['check'] for x in report['checks'] if not x['passed']}

    def test_valid_config_has_no_live_acceptance_and_no_secret_output(self):
        report, calls = self.run_check()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['compose_checked'])
        for field in ('release_accepted', 'changes_applied', 'telegram_verified', 'database_verified',
                      'admin_verified', 'payments_verified', 'sponsor_verified'):
            self.assertFalse(report[field])
        rendered = json.dumps(report)
        for key in ('POSTGRES_PASSWORD', 'BOT_INTERNAL_SECRET', 'SESSION_SIGNING_KEY', 'TELEGRAM_BOT_TOKEN', 'DATABASE_URL'):
            self.assertNotIn(self.values[key], rendered)
        self.assertEqual([c[0][0] for c in calls], ['git', 'docker'])
        self.assertEqual(calls[-1][0][-5:], ['--profile', 'bot', 'config', '--format', 'json'])
        self.assertEqual(calls[-1][1]['timeout'], 20)
        self.assertEqual(calls[0][1]['timeout'], 10)

    def test_permissions_rejected_before_docker(self):
        for mode in (0o644, 0o660, 0o700):
            with self.subTest(mode=mode):
                (self.root / '.env').chmod(mode)
                report, calls = self.run_check()
                self.assertIn('env_file', self.failed(report))
                self.assertEqual(len(calls), 1)

    def test_wrong_owner_rejected(self):
        with patch.object(preflight.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assertIn('env_file', self.failed(self.run_check()[0]))

    def test_symlink_rejected_without_reading_target(self):
        (self.root / '.env').rename(self.root / 'secret')
        (self.root / '.env').symlink_to(self.root / 'secret')
        report, calls = self.run_check()
        self.assertIn('env_file', self.failed(report))
        self.assertEqual(len(calls), 1)

    def test_missing_invalid_encoding_and_oversized_env(self):
        path = self.root / '.env'
        path.unlink()
        self.assertIn('env_file', self.failed(self.run_check()[0]))
        for data in (b'\xff', b'#' * 65537):
            path.write_bytes(data)
            path.chmod(0o600)
            self.assertIn('env_file', self.failed(self.run_check()[0]))

    def test_ambiguous_dotenv_rejected_without_exposing_source(self):
        for line in ('MALFORMED_SECRET', 'A=1\nA=SECRET', 'A="SECRET', 'A=${SECRET}',
                     'A=$(SECRET)', 'A=`SECRET`', 'A=one two SECRET', 'A="SECRET"junk', 'A="a\\nSECRET"'):
            with self.subTest(line=line):
                with self.assertRaises(preflight.ConfigError) as error:
                    preflight.parse_env(line)
                self.assertNotIn('SECRET', str(error.exception))

    def test_literal_quoted_values_and_comments(self):
        self.assertEqual(preflight.parse_env('# comment\nA="hello world" # ok\nB=abc#def\nC=abc # comment\nD=\n'),
                         {'A': 'hello world', 'B': 'abc#def', 'C': 'abc', 'D': ''})

    def test_duplicate_env_blocks_compose(self):
        with (self.root / '.env').open('a') as f:
            f.write('TELEGRAM_BOT_TOKEN=secret\n')
        report, calls = self.run_check()
        self.assertIn('env_file', self.failed(report))
        self.assertEqual(len(calls), 1)

    def test_placeholders_short_and_reused_secrets(self):
        for key, value, check in (
            ('TELEGRAM_BOT_TOKEN', 'REPLACE_WITH_BOT_TOKEN', 'required'),
            ('SESSION_SIGNING_KEY', 'short', 'secrets'),
            ('SESSION_SIGNING_KEY', 'c' * 64, 'secrets'),
            ('BOT_INTERNAL_SECRET', 'G' * 64, 'secrets')):
            with self.subTest(key=key, check=check):
                self.values = fixture()
                self.values[key] = value
                self.write_env()
                self.assertIn(check, self.failed(self.run_check()[0]))

    def test_database_credentials_host_and_port_must_match(self):
        for dsn in ('postgresql://mtproxy_pushok:wrong@db:5432/mtproxy_pushok',
                    self.values['DATABASE_URL'].replace('@db:', '@prod.example:'),
                    self.values['DATABASE_URL'].replace(':5432/', ':bad/'),
                    self.values['DATABASE_URL'] + '?host=prod.example'):
            self.values['DATABASE_URL'] = dsn
            self.write_env()
            self.assertIn('database', self.failed(self.run_check()[0]))

    def test_port_project_and_telegram_formats(self):
        for key, value, check in (('API_BIND_PORT', '0', 'api_port'), ('API_BIND_PORT', 'oops', 'api_port'),
                                 ('API_BIND_PORT', '65536', 'api_port'), ('COMPOSE_PROJECT_NAME', '../x', 'project_name'),
                                 ('TELEGRAM_BOT_TOKEN', '0:abcdefghij', 'telegram'),
                                 ('TELEGRAM_BOT_USERNAME', '@test_bot', 'telegram')):
            self.values = fixture()
            self.values[key] = value
            self.write_env()
            self.assertIn(check, self.failed(self.run_check()[0]))

    def test_flags_reject_typos(self):
        for key in preflight.FLAGS:
            self.values = fixture()
            self.values[key] = 'true'
            self.write_env()
            self.assertIn('feature_flags', self.failed(self.run_check()[0]))

    def test_miniapp_url_constraints(self):
        self.values['PUSHOK_MINIAPP_ENABLED'] = '1'
        for url in ('http://ads.example.com/miniapp', 'https://user@ads.example.com/miniapp',
                    'https://ads.example.com:444/miniapp', 'https://ads.example.com/miniapp?q=1',
                    'https://127.0.0.1/miniapp', 'https://ads.example.com/other'):
            self.values['PUSHOK_MINIAPP_URL'] = url
            self.write_env()
            self.assertIn('miniapp', self.failed(self.run_check()[0]))
        self.values['PUSHOK_MINIAPP_URL'] = 'https://ads.example.com/miniapp'
        self.write_env()
        self.assertTrue(self.run_check()[0]['ok'])

    def test_sbp_webhook_requires_credentials_and_reconciliation(self):
        self.values['PUSHOK_SBP_WEBHOOK_ENABLED'] = '1'
        self.write_env()
        self.assertTrue({'sbp_credentials', 'sbp_webhook'} <= self.failed(self.run_check()[0]))
        self.values.update(PUSHOK_SBP_RECONCILIATION_ENABLED='1', PUSHOK_SBP_PAYMENTS_ENABLED='0',
                           YOOKASSA_SHOP_ID='12345', YOOKASSA_SECRET_KEY='TEST_ONLY',
                           PUSHOK_PUBLIC_PAYMENT_HOST='ads.example.com')
        self.write_env()
        self.assertTrue(self.run_check()[0]['ok'])

    def test_git_tracking_and_unavailable_git_block_compose(self):
        for code, output in ((0, b'.env\n'), (128, b'')):
            report, calls = self.run_check(git_code=code, git_output=output)
            self.assertIn('env_not_tracked', self.failed(report))
            self.assertEqual(len(calls), 1)

    def test_git_unavailable_or_timeout_fails_closed(self):
        for error in (FileNotFoundError('SECRET'), subprocess.TimeoutExpired('git', 10, output=b'SECRET')):
            with patch.object(preflight.subprocess, 'run', side_effect=error) as run:
                report = preflight.inspect(self.root)
            self.assertIn('env_not_tracked', self.failed(report))
            self.assertEqual(run.call_count, 1)
            self.assertFalse(report['compose_checked'])
            self.assertNotIn('SECRET', json.dumps(report))

    def test_compose_published_database_host_network_and_public_api_rejected(self):
        base = compose(self.values)
        for service, prop, value in (
            ('db', 'ports', [{'target': 5432}]), ('bot', 'network_mode', 'host'),
            ('sbp-worker', 'privileged', True),
            ('api', 'ports', [{'host_ip': '0.0.0.0', 'target': 8000, 'published': '8000'}])):
            config = copy.deepcopy(base)
            config['services'][service][prop] = value
            self.assertIn('compose', self.failed(self.run_check(config=config)[0]))

    def test_compose_cannot_override_secrets_or_enable_absent_flags(self):
        for key, value in (('BOT_INTERNAL_SECRET', 'wrong'), ('PUSHOK_STARS_PAYMENTS_ENABLED', '1')):
            config = compose(self.values)
            config['services']['api']['environment'][key] = value
            self.assertIn('compose', self.failed(self.run_check(config=config)[0]))

    def test_docker_failure_timeout_and_bad_json_are_redacted(self):
        for error in (FileNotFoundError('SECRET'), subprocess.TimeoutExpired('SECRET', 20, output=b'SECRET')):
            report, _ = self.run_check(docker_error=error)
            self.assertIn('compose', self.failed(report))
            self.assertNotIn('SECRET', json.dumps(report))
        with patch.object(preflight.subprocess, 'run', side_effect=[
            subprocess.CompletedProcess([], 0, b'', b''),
            subprocess.CompletedProcess([], 1, b'SECRET', b'SECRET')]):
            report = preflight.inspect(self.root)
        self.assertFalse(report['ok'])
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertFalse(self.run_check(config={'services': []})[0]['ok'])

    def test_inherited_overrides_ignored_and_installer_override_included(self):
        (self.root / 'compose.override.yaml').write_text('services: {}')
        with patch.dict(os.environ, {'COMPOSE_FILE': '/other/compose.yml', 'GIT_INDEX_FILE': '/other/index',
                                     'TELEGRAM_BOT_TOKEN': 'SECRET'}):
            _, calls = self.run_check()
        self.assertIn(str(self.root / 'compose.override.yaml'), calls[-1][0])
        for _, kwargs in calls:
            self.assertNotIn('COMPOSE_FILE', kwargs['env'])
            self.assertNotIn('GIT_INDEX_FILE', kwargs['env'])
            self.assertNotIn('TELEGRAM_BOT_TOKEN', kwargs['env'])

    def test_config_only_explicitly_skips_compose(self):
        report, calls = self.run_check(config_only=True)
        self.assertTrue(report['ok'])
        self.assertFalse(report['compose_checked'])
        self.assertEqual(report['scope'], 'configuration_only')
        self.assertEqual(len(calls), 1)

    @unittest.skipUnless(shutil.which('git'), 'CLI integration requires Git; runs on the host CI runner')
    def test_cli_json_exit_status_and_no_file_changes(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        script = ROOT / 'scripts/staging_preflight.sh'
        before = (self.root / '.env').read_bytes()
        cmd = ['bash', str(script), '--root', str(self.root), '--config-only', '--json']
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertEqual(before, (self.root / '.env').read_bytes())
        self.values['PUSHOK_STARS_PAYMENTS_ENABLED'] = 'yes'
        self.write_env()
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertEqual(result.stderr, '')
        self.assertNotIn(self.values['TELEGRAM_BOT_TOKEN'], result.stdout)


if __name__ == '__main__':
    unittest.main()
