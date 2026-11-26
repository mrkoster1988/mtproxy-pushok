"""Verify automatic stable publication cannot bypass CI or replace VPS secrets."""
from pathlib import Path
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("enable_auto_updates", ROOT / "scripts/enable_auto_updates.py")
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class ReleasePromotionTests(unittest.TestCase):
    def test_workflow_only_promotes_successful_main_push(self):
        content = (ROOT / ".github/workflows/promote-tested-stable.yml").read_text()
        self.assertIn('workflow_run.conclusion == \'success\'', content)
        self.assertIn("workflow_run.event == 'push'", content)
        self.assertIn("workflow_run.head_branch == 'main'", content)
        self.assertIn('CI_SHA" != "$current"', content)
        self.assertIn('git merge-base --is-ancestor "$stable" "$current"', content)
        self.assertIn('-F force=false', content)
        self.assertIn('if: steps.proof.outputs.skip == \'false\'', content)
        self.assertIn('No green exact CI for current main', content)
        self.assertNotIn('--force-with-lease=false', content)

    def test_v129_version_and_auto_release_docs_agree(self):
        readme = (ROOT / 'README.md').read_text()
        api = (ROOT / 'api/app/main.py').read_text()
        core = (ROOT / 'api/app/core_readiness.py').read_text()
        installer = (ROOT / 'setup.sh').read_text()
        web = (ROOT / 'api/web/index.html').read_text()
        self.assertIn('Версия проекта: v2.24.', readme)
        self.assertIn('version="2.24",', api)
        self.assertEqual(core.count('"project_version": "2.24"'), 2)
        self.assertIn('MTProxy Pushok v2.24', installer)
        self.assertIn('v2.24</span>', web)
        self.assertIn('docs/RELEASE_v2.24.md', readme)

    def test_plan_is_read_only_and_requires_protected_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / ".control"
            control.mkdir(mode=0o700)
            control.chmod(0o700)
            with patch.object(auto, 'ROOT', root), patch.object(auto, 'CONTROL', control), \
                 patch.object(auto, 'CONFIG', control / 'auto-update.json'), \
                 patch.object(auto, 'unit_exists', return_value=True):
                self.assertFalse(auto.plan()['automatic_updates_enabled'])
                self.assertFalse((control / 'auto-update.json').exists())
                (control / 'auto-update.json').write_text('{"enabled": false, "note": "preserve"}')
                self.assertFalse(auto.plan()['automatic_updates_enabled'])
                (control / 'auto-update.json').write_text('{"enabled": "yes"}')
                with self.assertRaises(auto.Rejected):
                    auto.plan()

    def test_flag_write_preserves_other_settings_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / ".control"
            control.mkdir(mode=0o700)
            control.chmod(0o700)
            with patch.object(auto, 'CONTROL', control), patch.object(auto, 'CONFIG', control / 'auto-update.json'):
                auto.safe_save({'enabled': True, 'last_check': 'preserved'})
                data = json.loads((control / 'auto-update.json').read_text())
                self.assertEqual(data, {'enabled': True, 'last_check': 'preserved'})
                self.assertEqual((control / 'auto-update.json').stat().st_mode & 0o777, 0o600)

    def test_symlink_config_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / ".control"
            control.mkdir(mode=0o700)
            control.chmod(0o700)
            target = root / "unrelated"
            target.write_text('{"enabled":false}')
            (control / "auto-update.json").symlink_to(target)
            with patch.object(auto, "ROOT", root), patch.object(auto, "CONTROL", control), \
                 patch.object(auto, "CONFIG", control / "auto-update.json"):
                with self.assertRaises(auto.Rejected):
                    auto.existing()


if __name__ == '__main__':
    unittest.main()
