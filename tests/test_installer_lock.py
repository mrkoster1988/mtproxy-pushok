"""Конкурентный мастер останавливается до вызова пакетов и Docker."""
import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('flock'), 'requires util-linux flock')
class InstallerLockTests(unittest.TestCase):
    def test_second_installer_cannot_change_locked_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / 'setup.sh', root / 'setup.sh')
            binary = root / 'bin'
            binary.mkdir()
            for name, content in {'id': '#!/bin/sh\necho 0\n', 'apt-get': '#!/bin/sh\nexit 97\n'}.items():
                path = binary / name
                path.write_text(content)
                path.chmod(0o700)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ.get('PATH', ''))
                result = subprocess.run(['bash', str(root / 'setup.sh')], env=env,
                    input='2\n2\n\n\n2\n2\n1\n', text=True, capture_output=True, timeout=10)
            finally:
                os.close(fd)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('уже работает установщик', result.stderr)
            self.assertFalse((root / '.env').exists())
            self.assertFalse((root / '.backups').exists())

    def test_env_symlink_is_rejected_before_reading_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / 'setup.sh', root / 'setup.sh')
            (root / 'secret').write_text('KEEP_PRIVATE')
            (root / '.env').symlink_to(root / 'secret')
            result = subprocess.run(['bash', str(root / 'setup.sh'), '--plan'],
                                    input='2\n', text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('KEEP_PRIVATE', result.stdout + result.stderr)
            self.assertEqual((root / 'secret').read_text(), 'KEEP_PRIVATE')
