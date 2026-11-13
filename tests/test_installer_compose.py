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
        values = fixture()
        values['COMPOSE_PROJECT_NAME'] = 'pushok-installer-test-' + uuid4().hex[:12]
        runtime.atomic_env(cls.root, ''.join(f'{key}={value}\n' for key, value in values.items()))
        cls.addClassCleanup(cls.cleanup_compose)
        runtime.run(cls.root, ['up', '-d', '--wait', '--wait-timeout', '90', 'db'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)

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
        runtime.migrate(self.root, names)
        last = self.root / names[-1]
        last.write_text(last.read_text() + '\nCREATE TABLE installer_should_rollback (id integer);\nSELECT 1/0;\n')
        with self.assertRaises(subprocess.CalledProcessError):
            runtime.migrate(self.root, names)
        self.assertEqual(self.sql("SELECT to_regclass('installer_should_rollback') IS NULL;"), 't')
        self.assertEqual(self.sql('SELECT id FROM installer_probe;'), '42')
        self.assertTrue(dump.exists())
