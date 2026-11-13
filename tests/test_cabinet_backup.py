"""Копии кабинета: целостность, отказ без downtime и безопасные границы restore."""
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import cabinet_backup as backup
from test_staging_preflight import fixture


COUNTS = {'users': 1, 'campaigns': 0, 'payment_attempts': 0}


def tar_bytes(items=None):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w') as archive:
        for name, kind, data in items or [('proof.txt', 'file', b'PRIVATE_PROOF'), ('placement-evidence/a.txt', 'file', b'EVIDENCE')]:
            item = tarfile.TarInfo(name)
            if kind == 'file':
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            elif kind == 'dir':
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            else:
                item.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE if kind == 'hardlink' else tarfile.FIFOTYPE
                item.linkname = '/outside'
                archive.addfile(item)
    return target.getvalue()


def make_bundle(path, archive=None):
    path.mkdir(mode=0o700)
    data = {'database.dump': b'PGDMPtest-only', 'proofs.tar': tar_bytes() if archive is None else archive,
            'config.env': b'PRIVATE_SECRET=not-executed\n', 'compose.yaml': b'services: {}\n'}
    for name, content in data.items():
        backup.write(path / name, content)
    manifest = {'format': 'pushok-cabinet-backup-v1', 'source_commit': 'a'*40,
                'consistency': 'compose_writers_stopped', 'release_accepted': False,
                'files': {name: backup.digest(path / name) for name in data},
                'database_counts': dict(COUNTS), 'proofs': {'files': 2, 'bytes': 21}}
    backup.write(path / 'manifest.json', json.dumps(manifest).encode())
    return manifest


class CabinetBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'cabinet-test'
        self.manifest = make_bundle(self.bundle)

    def manifest_write(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_valid_bundle_verifies_and_files_restore_without_config_execution(self):
        report = backup.verify(self.bundle)
        self.assertFalse(report['release_accepted'])
        destination = self.root / 'restored'
        destination.mkdir(mode=0o700)
        proof = backup.proof_members(self.bundle / 'proofs.tar', destination)
        self.assertEqual(proof, self.manifest['proofs'])
        self.assertEqual((destination / 'proof.txt').read_bytes(), b'PRIVATE_PROOF')
        self.assertEqual((destination / 'placement-evidence/a.txt').read_bytes(), b'EVIDENCE')
        self.assertFalse((destination / 'config.env').exists())
        self.assertEqual(stat.S_IMODE((destination / 'proof.txt').stat().st_mode), 0o600)

    def test_hash_size_missing_and_extra_files_are_rejected(self):
        original = (self.bundle / 'database.dump').read_bytes()
        (self.bundle / 'database.dump').write_bytes(original + b'CORRUPT')
        with self.assertRaises(backup.BackupError): backup.verify(self.bundle)
        (self.bundle / 'database.dump').write_bytes(original)
        backup.write(self.bundle / 'extra', b'extra')
        with self.assertRaises(backup.BackupError): backup.verify(self.bundle)
        (self.bundle / 'extra').unlink()
        (self.bundle / 'config.env').unlink()
        with self.assertRaises(backup.BackupError): backup.verify(self.bundle)

    def test_manifest_cannot_reference_arbitrary_paths_or_change_schema(self):
        for key, value in [('format', 'unknown'), ('release_accepted', True), ('source_commit', '../bad'),
                           ('database_counts', {'users': -1}), ('files', {'../secret': {}})]:
            original = self.manifest[key]
            self.manifest[key] = value
            self.manifest_write()
            with self.assertRaises(backup.BackupError): backup.verify(self.bundle)
            self.manifest[key] = original

    def test_private_permissions_and_symlinks_required(self):
        path = self.bundle / 'config.env'
        path.chmod(0o644)
        with self.assertRaises(backup.BackupError): backup.verify(self.bundle)
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(self.root / 'outside')
        with self.assertRaises(OSError): backup.verify(self.bundle)

    def test_bundle_directory_symlink_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(backup.BackupError): backup.verify(link)

    def test_unsafe_tar_paths_types_duplicates_and_conflicts_rejected(self):
        cases = [
            [('../escape', 'file', b'x')], [('/absolute', 'file', b'x')], [('a\\b', 'file', b'x')],
            [('link', 'symlink', b'')], [('link', 'hardlink', b'')], [('fifo', 'fifo', b'')],
            [('same', 'file', b'a'), ('./same', 'file', b'b')],
            [('parent', 'file', b'a'), ('parent/child', 'file', b'b')],
            [('parent/child', 'file', b'a'), ('parent', 'file', b'b')],
        ]
        for items in cases:
            with self.subTest(items=items):
                path = self.bundle / 'proofs.tar'
                path.write_bytes(tar_bytes(items))
                with self.assertRaises(backup.BackupError): backup.proof_members(path)
        self.assertFalse((self.root / 'escape').exists())

    def test_truncated_tar_data_rejected(self):
        path = self.bundle / 'proofs.tar'
        path.write_bytes(tar_bytes([('long', 'file', b'x'*4096)])[:600])
        with self.assertRaises((backup.BackupError, tarfile.TarError)):
            backup.proof_members(path)

    def test_downtime_refusal_does_not_stop_or_start_services(self):
        with patch.object(backup, 'running', return_value={'db', 'api', 'bot'}), patch.object(backup, 'compose') as compose:
            with self.assertRaises(backup.BackupError), backup.quiesced(self.root, False):
                self.fail('must not enter')
        compose.assert_not_called()

    def test_only_initially_running_writers_resume_after_error(self):
        with patch.object(backup, 'running', side_effect=[{'db', 'api', 'bot'}, {'db'}, {'db', 'api', 'bot'}]), \
             patch.object(backup, 'compose') as compose:
            with self.assertRaisesRegex(RuntimeError, 'synthetic'), backup.quiesced(self.root, True):
                raise RuntimeError('synthetic')
        self.assertEqual(compose.call_args_list[0].args[1:], ('stop', '--timeout', '30', 'api', 'bot'))
        self.assertEqual(compose.call_args_list[1].args[1:], ('start', 'api', 'bot'))
        self.assertNotIn('sbp-worker', str(compose.call_args_list))

    def test_partial_stop_failure_still_resumes_initial_services(self):
        with patch.object(backup, 'running', side_effect=[{'db', 'api'}, {'db', 'api'}]), \
             patch.object(backup, 'compose', side_effect=[subprocess.CalledProcessError(1, ['secret']), Mock()]) as compose:
            with self.assertRaises(subprocess.CalledProcessError), backup.quiesced(self.root, True):
                self.fail('must not enter')
        self.assertEqual(compose.call_args_list[-1].args[1:], ('start', 'api'))

    def test_unknown_service_and_missing_database_refused(self):
        for active in ({'api'}, {'db', 'unknown-writer'}):
            with patch.object(backup, 'running', return_value=active), patch.object(backup, 'compose') as compose:
                with self.assertRaises(backup.BackupError), backup.quiesced(self.root, True):
                    self.fail('must not enter')
                compose.assert_not_called()

    def test_lock_conflicts_with_other_backup_or_installer(self):
        with backup.locked(self.root):
            with self.assertRaises(backup.BackupError), backup.locked(self.root):
                self.fail('must not enter')

    def test_create_refusal_does_not_create_backup_directory(self):
        with patch.object(backup, 'read_env', return_value={}), patch.object(backup, 'proof_source', return_value=('volume', 'image')), \
             patch.object(backup, 'running', return_value={'db', 'api'}):
            with self.assertRaises(backup.BackupError): backup.create(self.root)
        self.assertFalse((self.root / '.backups').exists())

    def simulate_create(self, *, failure=False):
        env = ''.join(f'{k}={v}\n' for k, v in fixture().items())
        backup.write(self.root / '.env', env.encode())
        (self.root / 'compose.yaml').write_text('services: {}\n')
        def compose(root, *args, **kwargs):
            if args[0] == 'exec' and args[-1].startswith('exec pg_dump'):
                kwargs['stdout'].write(b'PGDMPfixture')
            return Mock(stdout=b'')
        def docker(*args, **kwargs):
            if failure: raise OSError('PRIVATE_SECRET')
            kwargs['stdout'].write(tar_bytes())
            return Mock(stdout=b'')
        states = [{'db','api'}, {'db'}, {'db','api'}] if failure else [{'db','api'}, {'db'}, {'db'}, {'db','api'}]
        with patch.object(backup, 'proof_source', return_value=('volume', 'sha256:' + 'a'*64)), \
             patch.object(backup, 'running', side_effect=states), patch.object(backup, 'counts', return_value=dict(COUNTS)), \
             patch.object(backup, 'compose', side_effect=compose) as commands, patch.object(backup, 'docker', side_effect=docker), \
             patch.object(backup, 'cleanup_owned'), patch.object(backup, 'execute', return_value=Mock(stdout=b'a'*40)):
            if failure:
                with self.assertRaises(OSError): backup.create(self.root, allow_downtime=True)
                result = None
            else:
                result = backup.create(self.root, allow_downtime=True)
        return result, commands

    def test_create_publishes_complete_private_bundle_only(self):
        result, commands = self.simulate_create()
        self.assertTrue(result.name.startswith('cabinet-'))
        backup.verify(result)
        self.assertFalse(list(result.parent.glob('.incomplete-*')))
        self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o700)
        self.assertEqual(commands.call_args_list[-1].args[1:], ('start', 'api'))

    def test_failed_create_cleans_partial_files_and_keeps_old_backups(self):
        parent = self.root / '.backups'
        parent.mkdir(mode=0o700)
        backup.write(parent / 'old.dump', b'OLD')
        _, commands = self.simulate_create(failure=True)
        self.assertEqual([p.name for p in parent.iterdir()], ['old.dump'])
        self.assertEqual(commands.call_args_list[-1].args[1:], ('start', 'api'))

    def test_cleanup_requires_exact_name_label_and_id(self):
        ident = 'b'*64
        bad = {'Id':ident,'Name':'/owned','Config':{'Labels':{backup.LABEL:'other'}}}
        with patch.object(backup, 'docker', side_effect=[Mock(stdout=ident.encode()), Mock(stdout=json.dumps([bad]).encode())]) as docker:
            with self.assertRaises(backup.BackupError): backup.cleanup_owned('owned', backup.LABEL, 'token')
        self.assertFalse(any(call.args[0] == 'rm' for call in docker.call_args_list))
        bad['Config']['Labels'][backup.LABEL] = 'token'
        with patch.object(backup, 'docker', side_effect=[Mock(stdout=ident.encode()), Mock(stdout=json.dumps([bad]).encode()), Mock()]) as docker:
            backup.cleanup_owned('owned', backup.LABEL, 'token')
        self.assertEqual(docker.call_args.args, ('rm', '--force', '--volumes', ident))

    def test_drill_is_network_isolated_does_not_execute_saved_configuration(self):
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('image','inspect'): return Mock(stdout=json.dumps([{'Id':'sha256:'+'a'*64}]).encode())
            if args[0] == 'run': return Mock(stdout=b'b'*64)
            if 'psql' in args: return Mock(stdout=json.dumps(COUNTS).encode())
            return Mock(stdout=b'')
        with patch.object(backup, 'docker', side_effect=docker), patch.object(backup, 'cleanup_owned') as cleanup:
            report = backup.drill(self.bundle)
        self.assertTrue(report['database_restored'])
        self.assertFalse(report['configuration_executed'])
        self.assertFalse(report['release_accepted'])
        launch = next(args for args in calls if args[0] == 'run')
        self.assertEqual(launch[launch.index('--network')+1], 'none')
        self.assertNotIn('--publish', launch)
        self.assertNotIn('--env-file', launch)
        self.assertNotIn('compose', str(calls))
        cleanup.assert_called_once()

    def test_drill_failure_still_attempts_owned_cleanup(self):
        def docker(*args, **kwargs):
            if args[0] == 'image': return Mock(stdout=json.dumps([{'Id':'sha256:'+'a'*64}]).encode())
            if args[0] == 'run': return Mock(stdout=b'b'*64)
            if 'pg_restore' in args: raise subprocess.CalledProcessError(1, ['PRIVATE_SECRET'])
            return Mock(stdout=b'')
        with patch.object(backup, 'docker', side_effect=docker), patch.object(backup, 'cleanup_owned') as cleanup:
            with self.assertRaises(subprocess.CalledProcessError): backup.drill(self.bundle)
        cleanup.assert_called_once()

    def test_cli_errors_hide_secret_and_subprocess_output(self):
        with patch.object(sys, 'argv', ['cabinet_backup.py','verify',str(self.bundle)]), \
             patch.object(backup, 'verify', side_effect=ValueError('PRIVATE_SECRET')), \
             patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(backup.main(), 1)
        report = json.loads(stderr.getvalue())
        self.assertFalse(report['ok'])
        self.assertNotIn('PRIVATE_SECRET', stderr.getvalue())

    def test_menu_defaults_to_plan_and_cancel_does_not_create(self):
        with patch('builtins.input', return_value=''), patch.object(sys, 'stdout', new_callable=io.StringIO):
            self.assertEqual(backup.menu(), ('plan', None, False))
        with patch('builtins.input', side_effect=['2','3']), patch.object(sys, 'stdout', new_callable=io.StringIO):
            self.assertIsNone(backup.menu())


if __name__ == '__main__':
    unittest.main()
