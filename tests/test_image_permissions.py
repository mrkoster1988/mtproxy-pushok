"""Настоящие Docker-образы из закрытого checkout, как у systemd UMask=0077."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('PUSHOK_IMAGE_PERMISSION_TESTS') == '1',
                     'opt-in isolated Docker image permissions regression')
class ImagePermissionsTests(unittest.TestCase):
    def docker(self, *args, **kwargs):
        return subprocess.run(['docker', *args], check=True, timeout=600, **kwargs)

    def test_private_checkout_runs_as_nonroot_and_navigation_stays_private(self):
        prefix = 'pushok-permissions-' + uuid4().hex
        images = [prefix + '-api', prefix + '-bot']
        volume = prefix + '-navigation'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('api', 'bot'):
                shutil.copytree(ROOT / name, root / name,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            secret = root / 'api' / '.env'
            secret.write_text('SYNTHETIC_SECRET=not-a-real-secret\n')
            # Не меняем рабочее дерево CI: дефект воспроизводится в отдельной копии.
            for path in sorted(root.rglob('*'), reverse=True):
                path.chmod(0o700 if path.is_dir() else 0o600)
            try:
                self.docker('build', '-t', images[0], str(root / 'api'))
                self.docker('build', '-f', str(root / 'bot' / 'Dockerfile'),
                            '-t', images[1], str(root))
                api_check = '''
import os,stat
from pathlib import Path
assert os.geteuid()==10001
from app.main import app
for base in ('app','web'):
 for path in Path(base).rglob('*'):
  if path.is_file(): path.read_bytes()
assert '<html' in Path('web/index.html').read_text()
assert not Path('.env').exists()
assert os.stat('/var/lib/mtproxy-pushok/payment-proofs').st_uid==10001
print('API source and assets readable as UID 10001')
'''
                self.docker('run','--rm','--network','none','--read-only',
                    '-e','DATABASE_URL=postgresql://unused',images[0],'python','-c',api_check)
                self.docker('volume','create',volume,stdout=subprocess.DEVNULL)
                bot_check = '''
import os,stat
from pathlib import Path
import bot.main
from bot.navigation import Navigation
assert os.geteuid()==10001
for path in Path('/app/bot').rglob('*.py'): path.read_bytes()
folder=Path('/var/lib/mtproxy-pushok/navigation')
assert stat.S_IMODE(folder.stat().st_mode)==0o700
assert folder.stat().st_uid==10001
nav=Navigation(lambda *a:None,folder/'test.sqlite')
nav.save(77,nav.state(77))
assert stat.S_IMODE((folder/'test.sqlite').stat().st_mode)==0o600
nav.db.close()
assert not Path('/app/.env').exists()
print('Bot source readable and navigation volume private as UID 10001')
'''
                self.docker('run','--rm','--network','none','--read-only',
                    '--mount','type=volume,source='+volume+',target=/var/lib/mtproxy-pushok/navigation',
                    images[1],'python','-c',bot_check)
                self.assertEqual(secret.stat().st_mode & 0o777,0o600)
            finally:
                # Удаляются только уникальные тестовые объекты, без glob/prune.
                subprocess.run(['docker','volume','rm',volume],check=False,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
                subprocess.run(['docker','image','rm','-f',*images],check=False,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
