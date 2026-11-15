"""Приёмка dump/restore и отката SQL только в уникальном временном Compose."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from test_installer_runtime import ROOT, runtime
from test_staging_preflight import fixture


@unittest.skipUnless(os.getenv('PUSHOK_INSTALLER_COMPOSE_TESTS') == '1', 'opt-in disposable installer database')
class InstallerComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        shutil.copyfile(ROOT / 'compose.yaml', cls.root / 'compose.yaml')
        shutil.copytree(ROOT / 'database', cls.root / 'database')
        # Имитируем установленную v1.09: миграции до 031, без нового журнала.
        for path in (cls.root / 'database').glob('*.sql'):
            if int(path.name[:3]) > 31:
                path.unlink()
        values = fixture()
        values['COMPOSE_PROJECT_NAME'] = 'pushok-installer-test-' + uuid4().hex[:12]
        runtime.atomic_env(cls.root, ''.join(f'{key}={value}\n' for key, value in values.items()))
        cls.addClassCleanup(cls.cleanup_compose)
        runtime.run(cls.root, ['up', '-d', '--wait', '--wait-timeout', '90', 'db'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        for path in (ROOT / 'database').glob('*.sql'):
            shutil.copyfile(path, cls.root / 'database' / path.name)

    @classmethod
    def cleanup_compose(cls):
        runtime.run(cls.root, ['down', '--volumes', '--remove-orphans'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)

    def sql(self, statement, database=None):
        command = 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d '
        command += 'pushok_restored' if database else '"$POSTGRES_DB"'
        return runtime.run(self.root, ['exec', '-T', 'db', 'sh', '-c', command],
                           input=statement.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30).stdout.decode().strip()

    def test_private_backup_restores_data_and_failed_migration_rolls_back(self):
        self.sql('CREATE TABLE installer_probe (id integer primary key); INSERT INTO installer_probe VALUES (42);')
        self.sql("INSERT INTO audit_events(event_type) VALUES ('placement_completed_by_agent');")
        # Точное старое ограничение из 030 воспроизводит сбой на данных v1.09.
        with self.assertRaises(subprocess.CalledProcessError):
            self.sql("BEGIN; ALTER TABLE audit_events DROP CONSTRAINT audit_events_system_actor_check; "
                     "ALTER TABLE audit_events ADD CONSTRAINT audit_events_system_actor_check "
                     "CHECK(actor_user_id IS NOT NULL OR event_type IN ('placement_auto_scheduled','placement_activated_by_agent')); COMMIT;")
        dump = runtime.backup_database(self.root)
        runtime.run(self.root, ['exec', '-T', 'db', 'sh', '-c',
                               'exec createdb -U "$POSTGRES_USER" pushok_restored'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        with dump.open('rb') as source:
            runtime.run(self.root, ['exec', '-T', 'db', 'sh', '-c',
                                   'exec pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d pushok_restored'],
                        stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(self.sql('SELECT id FROM installer_probe;', database='restored'), '42')
        names = sorted(path.relative_to(self.root).as_posix() for path in (self.root / 'database').glob('*.sql')
                       if not path.name.startswith('001_'))
        runtime.migrate(self.root, names)
        ledger = self.sql('SELECT name,sha256,applied_at FROM pushok_schema_migrations ORDER BY name;')
        runtime.migrate(self.root, names)
        self.assertEqual(self.sql('SELECT name,sha256,applied_at FROM pushok_schema_migrations ORDER BY name;'), ledger)
        self.assertEqual(self.sql("SELECT count(*) FROM audit_events WHERE event_type='placement_completed_by_agent';"), '1')
        self.assertEqual(self.sql("SELECT to_regclass('default_sponsor_settings') IS NOT NULL AND to_regclass('campaign_notice_outbox') IS NOT NULL;"), 't')
        last = self.root / names[-1]
        original = last.read_text()
        last.write_text(original + '\nSELECT 1;\n')
        with self.assertRaisesRegex(runtime.InstallError, 'P0001'):
            runtime.migrate(self.root, names)
        last.write_text(original)
        extra = self.root / 'database/998_upgrade_probe.sql'
        extra.write_text('CREATE TABLE installer_should_rollback (id integer);')
        broken = self.root / 'database/999_upgrade_failure.sql'
        broken.write_text('SELECT 1/0;')
        names += [str(extra.relative_to(self.root)), str(broken.relative_to(self.root))]
        with self.assertRaisesRegex(runtime.InstallError, '999_upgrade_failure.sql; SQLSTATE: 22012'):
            runtime.migrate(self.root, names)
        self.assertEqual(self.sql("SELECT to_regclass('installer_should_rollback') IS NULL;"), 't')
        self.assertEqual(self.sql('SELECT name,sha256,applied_at FROM pushok_schema_migrations ORDER BY name;'), ledger)
        broken.write_text('INSERT INTO installer_should_rollback VALUES(7);')
        runtime.migrate(self.root, names)
        runtime.migrate(self.root, names)
        self.assertEqual(self.sql('SELECT id FROM installer_should_rollback;'), '7')
        self.assertEqual(self.sql('SELECT id FROM installer_probe;'), '42')
        self.assertTrue(dump.exists())
