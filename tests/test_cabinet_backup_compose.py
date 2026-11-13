"""Полный backup/drill на искусственных данных, без портов и production .env."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from uuid import uuid4

from test_cabinet_backup import ROOT, backup
from test_staging_preflight import fixture


@unittest.skipUnless(os.getenv('PUSHOK_BACKUP_COMPOSE_TESTS') == '1', 'opt-in isolated cabinet backup/drill')
class CabinetBackupComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        values = fixture()
        values['COMPOSE_PROJECT_NAME'] = 'pushok-bundle-test-' + uuid4().hex[:12]
        backup.write(cls.root / '.env', ''.join(f'{k}={v}\n' for k, v in values.items()).encode())
        (cls.root / '.gitignore').write_text('.env\n.backups/\n')
        shutil.copytree(ROOT / 'database', cls.root / 'database')
        idle = {'image':'postgres:16-alpine', 'command':['sh','-c',"trap 'exit 0' TERM INT; while :; do sleep 1 & wait $$!; done"], 'networks':['isolated']}
        config = {'services': {
            'db': {'image':'postgres:16-alpine', 'environment':{k:values[k] for k in ('POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD')},
                   'volumes':['db-data:/var/lib/postgresql/data','./database:/docker-entrypoint-initdb.d:ro'], 'networks':['isolated'],
                   'healthcheck':{'test':['CMD-SHELL','pg_isready -h 127.0.0.1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'],
                                  'interval':'1s','timeout':'3s','retries':45}},
            'api':dict(idle, environment={'PAYMENT_PROOFS_DIR':backup.PROOFS}, volumes=['proofs:' + backup.PROOFS]),
            'sbp-worker':dict(idle), 'bot':dict(idle, profiles=['bot'])},
            'volumes':{'db-data':{},'proofs':{}}, 'networks':{'isolated':{'internal':True}}}
        (cls.root / 'compose.yaml').write_text(json.dumps(config))
        subprocess.run(['git','init','-q',str(cls.root)],check=True)
        subprocess.run(['git','-C',str(cls.root),'add','compose.yaml','database','.gitignore'],check=True)
        subprocess.run(['git','-C',str(cls.root),'-c','user.name=Pushok CI','-c','user.email=ci@example.invalid','commit','-qm','isolated fixture'],check=True)
        cls.addClassCleanup(cls.cleanup_compose)
        backup.compose(cls.root, 'up','-d','--wait','--wait-timeout','90','db','api','sbp-worker',timeout=120)
        backup.compose(cls.root,'exec','-T','db','sh','-c',
                       'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                       input=b"INSERT INTO users (telegram_user_id, first_name) VALUES (99123456, 'backup-fixture');",timeout=30)
        backup.compose(cls.root,'exec','-T','--user','0','api','sh','-c',
                       'mkdir -p "$PAYMENT_PROOFS_DIR/placement-evidence"; '
                       'printf proof-fixture > "$PAYMENT_PROOFS_DIR/proof.txt"; '
                       'printf evidence-fixture > "$PAYMENT_PROOFS_DIR/placement-evidence/a.txt"; '
                       'chown -R 10001:10001 "$PAYMENT_PROOFS_DIR"; chmod 700 "$PAYMENT_PROOFS_DIR"',timeout=30)

    @classmethod
    def cleanup_compose(cls):
        backup.compose(cls.root,'down','--volumes','--remove-orphans',timeout=90)

    def test_complete_copy_restores_database_files_and_preserves_service_state(self):
        initial = backup.running(self.root)
        self.assertNotIn('bot', initial)
        with self.assertRaises(backup.BackupError): backup.create(self.root)
        self.assertFalse((self.root / '.backups').exists())
        bundle = backup.create(self.root, allow_downtime=True)
        manifest = backup.verify(bundle)
        self.assertEqual(manifest['database_counts']['users'], 1)
        self.assertEqual(manifest['proofs']['files'], 2)
        self.assertEqual((bundle / 'config.env').read_bytes(), (self.root / '.env').read_bytes())
        self.assertEqual(backup.running(self.root), initial)
        report = backup.drill(bundle)
        self.assertTrue(report['database_restored'])
        self.assertEqual(report['proofs_restored']['files'], 2)
        self.assertFalse(report['configuration_executed'])
        self.assertFalse(report['release_accepted'])
        self.assertEqual(backup.counts(self.root)['users'], 1)
        self.assertEqual(backup.docker('ps','--all','--quiet','--filter','label=' + backup.LABEL).stdout.strip(), b'')
        # Повреждённый комплект не допускается к повторному restore.
        (bundle / 'database.dump').write_bytes(b'PGDMPcorrupt')
        with self.assertRaises(backup.BackupError): backup.drill(bundle)

    def test_unsafe_proof_aborts_copy_and_restarts_original_writers(self):
        initial = backup.running(self.root)
        before = set((self.root / '.backups').glob('cabinet-*'))
        backup.compose(self.root,'exec','-T','api','sh','-c',
                       'ln -s /etc/passwd "$PAYMENT_PROOFS_DIR/unsafe-link"',timeout=30)
        try:
            with self.assertRaises(backup.BackupError): backup.create(self.root, allow_downtime=True)
        finally:
            backup.compose(self.root,'exec','-T','api','sh','-c',
                           'rm -f "$PAYMENT_PROOFS_DIR/unsafe-link"',timeout=30)
        self.assertEqual(backup.running(self.root), initial)
        self.assertEqual(set((self.root / '.backups').glob('cabinet-*')), before)
        self.assertFalse(list((self.root / '.backups').glob('.incomplete-*')))
        self.assertEqual(backup.docker('ps','--all','--quiet','--filter','label=io.pushok.backup-read').stdout.strip(), b'')
