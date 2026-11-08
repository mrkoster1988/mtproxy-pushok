"""Защита предпускового сценария от опасных команд и раскрытия секретов."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StagingPreflightTests(unittest.TestCase):
    def test_script_is_syntax_valid(self):
        script = ROOT / "scripts/staging_preflight.sh"
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_does_not_mutate_containers_or_expose_secrets(self):
        script = (ROOT / "scripts/staging_preflight.sh").read_text(encoding="utf-8")
        self.assertIn("docker compose --profile bot config --quiet", script)
        self.assertIn("BOT_INTERNAL_SECRET", script)
        self.assertIn("TELEGRAM_BOT_TOKEN", script)
        self.assertIn("git ls-files --error-unmatch .env", script)
        for unsafe in ("docker compose up", "docker compose down",
                       "docker compose restart", "docker compose exec",
                       "docker compose run", "deleteWebhook",
                       "set -x", "env |", "print(values)", "echo $"):
            with self.subTest(command=unsafe):
                self.assertNotIn(unsafe, script)


if __name__ == "__main__":
    unittest.main()
