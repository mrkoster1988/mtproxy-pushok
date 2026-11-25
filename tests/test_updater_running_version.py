"""Regression: Git checkout must not masquerade as the version running in Docker."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))
import updater
from app import releases

CONTAINER = "abc123def456"


class RunningVersionTests(unittest.TestCase):
    def test_installed_version_is_from_running_container_not_new_readme(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
            self.assertFalse(kwargs["check"])
            self.assertEqual(kwargs["timeout"], 15)
            if argv[:2] == [sys.executable, "scripts/installer_runtime.py"]:
                self.assertEqual(argv[2:], ["compose", "ps", "-q", "api"])
                return subprocess.CompletedProcess(argv, 0, CONTAINER + "\n")
            self.assertEqual(argv[:3], ["docker", "exec", CONTAINER])
            self.assertEqual(argv[3:5], ["python3", "-c"])
            self.assertIn("/srv/mtproxy-pushok/app/main.py", argv[-1])
            self.assertIn("version", argv[-1])
            return subprocess.CompletedProcess(argv, 0, "1.127\n")

        with patch.object(updater.subprocess, "run", side_effect=fake_run), \
             patch.object(releases, "check", return_value={"state": "available"}) as release:
            result = updater.published_release()
        self.assertEqual(result["state"], "available")
        release.assert_called_once_with("1.127")
        self.assertEqual(len(calls), 2)

    def test_fails_closed_when_no_running_container_exists(self):
        for output in ("", "wrong", "abc123def456\nabc123def457\n", "a" * 65):
            with self.subTest(output=output), patch.object(updater.subprocess, "run",
                     return_value=subprocess.CompletedProcess([], 0, output)):
                with self.assertRaisesRegex(ValueError, "running_api_version_unavailable"):
                    updater.running_api_version()

    def test_fails_closed_if_docker_unavailable(self):
        with patch.object(updater.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1, "")):
            with self.assertRaisesRegex(ValueError, "running_api_version_unavailable"):
                updater.running_api_version()

    def test_fails_closed_for_unreadable_or_invalid_app_version(self):
        outputs = iter([CONTAINER, "not-a-version"])
        with patch.object(updater.subprocess, "run", side_effect=lambda *a, **k:
                          subprocess.CompletedProcess(a[0], 0, next(outputs))):
            with self.assertRaises(ValueError):
                updater.running_api_version()

    def test_rejects_malformed_container_version_while_source_may_be_newer(self):
        for code in ("", "1.12.a", "v1.127", "1.127\n1.128"):
            with self.subTest(code=code):
                outputs = iter([CONTAINER, code])
                with patch.object(updater.subprocess, "run", side_effect=lambda *a, **k:
                                  subprocess.CompletedProcess(a[0], 0, next(outputs))):
                    with self.assertRaises(ValueError):
                        updater.running_api_version()


if __name__ == "__main__":
    unittest.main()
