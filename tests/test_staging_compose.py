"""Real Compose config checks with synthetic credentials, no daemon or containers."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_staging_preflight import ROOT, fixture


@unittest.skipUnless(os.getenv('PUSHOK_COMPOSE_PREFLIGHT_TESTS') == '1', 'opt-in real Compose configuration test')
class StagingComposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        shutil.copyfile(ROOT / 'compose.yaml', self.root / 'compose.yaml')
        values = fixture()
        values.update(PUSHOK_MINIAPP_ENABLED='1', PUSHOK_MINIAPP_URL='https://ads.example.com/miniapp',
                      PUSHOK_TELEGRAM_TEST_ENVIRONMENT='1', PUSHOK_STARS_PAYMENTS_ENABLED='0',
                      PUSHOK_SBP_PAYMENTS_ENABLED='0', PUSHOK_SBP_TEST_MODE='1')
        self.env = self.root / '.env'
        self.env.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
        self.env.chmod(0o600)

    def check_config(self, expected):
        before = self.env.read_bytes()
        proc = subprocess.run(['bash', str(ROOT / 'scripts/staging_preflight.sh'), '--root', str(self.root), '--json'],
                              capture_output=True, text=True, timeout=40)
        report = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, expected, report)
        self.assertEqual(before, self.env.read_bytes())
        self.assertEqual(proc.stderr, '')
        self.assertFalse(report['release_accepted'])
        return report

    def test_installer_config_and_caddy_override(self):
        self.assertTrue(self.check_config(0)['compose_checked'])
        (self.root / 'compose.override.yaml').write_text('''services:
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
''')
        self.assertTrue(self.check_config(0)['compose_checked'])

    def test_deferred_telegram_matches_compose_defaults(self):
        text = self.env.read_text()
        text = text.replace('TELEGRAM_BOT_TOKEN=123456789:TEST_ONLY_secret_123456789', 'TELEGRAM_BOT_TOKEN=')
        text = text.replace('TELEGRAM_BOT_USERNAME=mtproxy_pushok_bot', 'TELEGRAM_BOT_USERNAME=')
        text = text.replace('PUSHOK_MINIAPP_ENABLED=1', 'PUSHOK_MINIAPP_ENABLED=0')
        text = text.replace('PUSHOK_TELEGRAM_TEST_ENVIRONMENT=1', 'PUSHOK_TELEGRAM_TEST_ENVIRONMENT=0')
        self.env.write_text(text)
        proc = subprocess.run(['bash', str(ROOT / 'scripts/staging_preflight.sh'), '--root', str(self.root),
                               '--allow-telegram-deferred', '--json'], capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(json.loads(proc.stdout)['telegram_verified'])
        self.check_config(1)  # Без явного разрешения пустой токен не принимается.

    def test_database_exposure_blocked(self):
        (self.root / 'compose.override.yaml').write_text('''services:
  db:
    ports: ["5432:5432"]
''')
        self.assertFalse(self.check_config(1)['ok'])

    def test_secret_override_blocked(self):
        (self.root / 'compose.override.yaml').write_text('''services:
  api:
    environment:
      BOT_INTERNAL_SECRET: different-secret-not-to-be-printed
''')
        report = self.check_config(1)
        self.assertNotIn('different-secret-not-to-be-printed', json.dumps(report))
