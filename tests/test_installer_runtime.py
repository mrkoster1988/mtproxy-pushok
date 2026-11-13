"""Проверки отказов мастера без пакетов, Docker и production-конфигурации."""
import base64
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import installer_runtime as runtime
from test_staging_preflight import fixture


class InstallerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.values = fixture()
        self.text = ''.join(f'{k}={v}\n' for k, v in self.values.items())

    def create(self):
        runtime.atomic_env(self.root, self.text)

    def test_private_atomic_create(self):
        self.create()
        self.assertEqual((self.root / '.env').read_text(), self.text)
        self.assertEqual(stat.S_IMODE((self.root / '.env').stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.root.iterdir()], ['.env'])

    def test_existing_file_or_symlink_is_never_overwritten(self):
        for symlink in (False, True):
            path = self.root / '.env'
            target = self.root / 'existing'
            target.write_text('keep me')
            if symlink:
                path.symlink_to(target)
            else:
                path.write_text('keep me')
            with self.assertRaises(FileExistsError):
                self.create()
            self.assertEqual(target.read_text(), 'keep me')
            self.assertEqual(path.read_text(), 'keep me')
            self.assertFalse(list(self.root.glob('.env.install-*')))
            path.unlink()

    def test_failed_write_does_not_publish_partial_env(self):
        with patch.object(runtime.os, 'fsync', side_effect=OSError('secret')):
            with self.assertRaises(OSError):
                self.create()
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse(list(self.root.glob('.env.install-*')))

    def test_invalid_config_does_not_create_any_file(self):
        with self.assertRaises(runtime.InstallError):
            runtime.atomic_env(self.root, 'POSTGRES_PASSWORD=REPLACE_ME\n')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_deferred_telegram_requires_all_features_off(self):
        data = dict(self.values, TELEGRAM_BOT_TOKEN='', TELEGRAM_BOT_USERNAME='')
        text = ''.join(f'{k}={v}\n' for k, v in data.items())
        runtime.validate(text)
        for key in ('PUSHOK_MINIAPP_ENABLED', 'PUSHOK_STARS_PAYMENTS_ENABLED', 'PUSHOK_TELEGRAM_TEST_ENVIRONMENT'):
            with self.subTest(key=key), self.assertRaises(runtime.InstallError):
                runtime.validate(text + key + '=1\n')
        with self.assertRaises(runtime.InstallError):
            runtime.validate(text.replace('TELEGRAM_BOT_TOKEN=\n', 'TELEGRAM_BOT_TOKEN=bad\n'))

    def test_payment_update_preserves_secrets_and_single_key(self):
        self.text += '# keep comment\nPAYMENT_INSTRUCTIONS_B64=\n'
        self.create()
        encoded = base64.b64encode('Тестовая инструкция'.encode()).decode()
        runtime.set_payment(self.root, encoded)
        changed = (self.root / '.env').read_text()
        for key in ('DATABASE_URL', 'SESSION_SIGNING_KEY', 'TELEGRAM_BOT_TOKEN'):
            self.assertIn(key + '=' + self.values[key], changed)
        self.assertIn('# keep comment', changed)
        self.assertEqual(changed.count('PAYMENT_INSTRUCTIONS_B64='), 1)
        self.assertIn(encoded, changed)
        self.assertEqual(stat.S_IMODE((self.root / '.env').stat().st_mode), 0o600)

    def test_invalid_payment_does_not_change_config(self):
        self.create()
        for encoded in ('%INVALID%', base64.b64encode(b'line1\nline2').decode(), base64.b64encode(b'x'*4001).decode()):
            with self.assertRaises(runtime.InstallError):
                runtime.set_payment(self.root, encoded)
            self.assertEqual((self.root / '.env').read_text(), self.text)

    def test_concurrent_change_is_not_replaced(self):
        self.create()
        before = (self.root / '.env').stat()
        (self.root / '.env').write_text(self.text + '# new edit\n')
        with self.assertRaises(runtime.InstallError):
            runtime.atomic_env(self.root, self.text, replace=True, expected_stat=before)
        self.assertIn('# new edit', (self.root / '.env').read_text())

    def test_backup_has_private_permissions_and_is_validated(self):
        self.create()
        calls = []
        def fake(root, args, **kwargs):
            calls.append(args)
            if 'pg_restore' not in args:
                kwargs['stdout'].write(b'PGDMPfake-test-dump')
        with patch.object(runtime, 'run', side_effect=fake):
            path = runtime.backup_database(self.root)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(calls[-1][-2:], ['pg_restore', '--list'])

    def test_backup_failure_removes_only_incomplete_dump(self):
        self.create()
        directory = self.root / '.backups'
        directory.mkdir(mode=0o700)
        older = directory / 'older.dump'
        older.write_bytes(b'KEEP')
        for failure in ('dump', 'validation'):
            def fake(root, args, **kwargs):
                if failure == 'validation' and 'pg_restore' not in args:
                    kwargs['stdout'].write(b'PGDMPfixture')
                    return
                raise subprocess.CalledProcessError(1, ['SECRET'])
            with patch.object(runtime, 'run', side_effect=fake), self.assertRaises(subprocess.CalledProcessError):
                runtime.backup_database(self.root)
            self.assertEqual(list(directory.iterdir()), [older])
            self.assertEqual(older.read_bytes(), b'KEEP')

    def test_backup_rejects_symlink_and_public_directory(self):
        for symlink in (False, True):
            directory = self.root / '.backups'
            if symlink:
                target = self.root / 'target'
                target.mkdir(mode=0o700)
                directory.symlink_to(target, target_is_directory=True)
            else:
                directory.mkdir(mode=0o755)
            with patch.object(runtime, 'run') as run, self.assertRaises(runtime.InstallError):
                runtime.backup_database(self.root)
            run.assert_not_called()
            if symlink: directory.unlink()
            else: directory.rmdir()

    def test_migrations_require_complete_ordered_list(self):
        (self.root / 'database').mkdir()
        for name in ('001_initial.sql', '002_one.sql', '003_two.sql'):
            (self.root / 'database' / name).write_text('SELECT 1;')
        names = ['database/002_one.sql', 'database/003_two.sql']
        with patch.object(runtime, 'run') as run:
            runtime.migrate(self.root, names)
        command = run.call_args.args[1][-1]
        self.assertIn('--single-transaction', command)
        self.assertIn('--file=-', command)
        self.assertIn('ON_ERROR_STOP=1', command)
        self.assertIn(b"lock_timeout = '10s'", run.call_args.kwargs['input'])
        for bad in (names[::-1], names[:1], ['../secret']):
            with self.assertRaises(runtime.InstallError):
                runtime.migration_sql(self.root, bad)

    def test_api_wait_requires_ready_database_and_no_redirects_or_proxy(self):
        self.create()
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"status":"ready","database":"ok"}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(runtime, 'build_opener', return_value=opener) as build:
            self.assertTrue(runtime.wait_api(self.root))
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], runtime.NoRedirect)
        self.assertIsNone(build.call_args.args[1].redirect_request(None, None, None, None, None, 'https://external.example'))
        self.assertEqual(opener.open.call_args.args[0], 'http://127.0.0.1:8000/health/ready')
        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 3)

    def test_unready_and_invalid_api_responses_time_out_without_printing_body(self):
        self.create()
        for raw in (b'{"status":"ready","database":"down"}', b'[]', b'SECRET', b'x'*65537):
            response = Mock(status=200)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw
            opener = Mock()
            opener.open.return_value = response
            with patch.object(runtime, 'build_opener', return_value=opener), \
                 patch.object(runtime.time, 'monotonic', side_effect=[0, 0, 0, 121, 121]), \
                 patch.object(runtime.time, 'sleep'):
                self.assertFalse(runtime.wait_api(self.root))

    def test_cli_redacts_command_exception(self):
        self.create()
        with patch.object(sys, 'argv', ['installer_runtime.py', '--root', str(self.root), 'backup']), \
             patch.object(runtime, 'backup_database', side_effect=subprocess.CalledProcessError(1, ['SECRET'], stderr='SECRET')), \
             patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(runtime.main(), 1)
        self.assertNotIn('SECRET', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
