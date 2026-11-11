"""Защита предпускового сценария от опасных команд и раскрытия секретов."""
import os
import subprocess
import tempfile
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
        self.assertIn("docker compose config --quiet", script)
        self.assertIn("--cabinet-only", script)
        self.assertIn("--with-bot", script)
        self.assertIn("BOT_INTERNAL_SECRET", script)
        self.assertIn("TELEGRAM_BOT_TOKEN", script)
        self.assertIn("git ls-files --error-unmatch .env", script)
        for unsafe in ("docker compose up", "docker compose down",
                       "docker compose restart", "docker compose exec",
                       "docker compose run", "deleteWebhook",
                       "set -x", "env |", "print(values)", "echo $"):
            with self.subTest(command=unsafe):
                self.assertNotIn(unsafe, script)

    def run_in_isolated_directory(self, mode, *, token="", username="",
                                  db_url=None, env_mode=0o600, stars="0"):
        """Fake Docker: check command choice without starting any service."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "scripts").mkdir()
            (base / "bin").mkdir()
            (base / "scripts/staging_preflight.sh").write_text(
                (ROOT / "scripts/staging_preflight.sh").read_text(encoding="utf-8"),
                encoding="utf-8")
            (base / ".env").write_text(
                "\n".join([
                    "POSTGRES_DB=pushok_test",
                    "POSTGRES_USER=pushok_test",
                    "POSTGRES_PASSWORD=not-real-test-secret",
                    "DATABASE_URL=" + (db_url or "postgresql://pushok_test:not-real-test-secret@db:5432/pushok_test"),
                    "BOT_INTERNAL_SECRET=" + "a" * 64,
                    "SESSION_SIGNING_KEY=" + "b" * 64,
                    "TELEGRAM_BOT_TOKEN=" + token,
                    "TELEGRAM_BOT_USERNAME=" + username,
                    "PUSHOK_STARS_PAYMENTS_ENABLED=" + stars,
                ]) + "\n", encoding="utf-8")
            (base / ".env").chmod(env_mode)
            docker = base / "bin/docker"
            docker.write_text(
                '#!/bin/sh\nprintf "%s\\\n" "$*" >> "$DOCKER_CALLS"\n',
                encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(base / "bin") + os.pathsep + env.get("PATH", "")
            env["DOCKER_CALLS"] = str(base / "docker-calls.txt")
            proc = subprocess.run(
                ["bash", str(base / "scripts/staging_preflight.sh"), mode],
                capture_output=True, text=True, env=env, check=False)
            calls = ((base / "docker-calls.txt").read_text(encoding="utf-8")
                     if (base / "docker-calls.txt").exists() else "")
            return proc, calls

    def test_cabinet_only_requires_no_telegram_token(self):
        proc, calls = self.run_in_isolated_directory("--cabinet-only")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("compose version", calls)
        self.assertIn("compose config --quiet", calls)
        self.assertNotIn("--profile bot", calls)

    def test_external_database_rejected_without_printing_credentials(self):
        secret = "secret-should-never-be-printed"
        proc, calls = self.run_in_isolated_directory(
            "--cabinet-only", db_url="postgresql://user:" + secret + "@prod.example.org:5432/postgres")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DATABASE_URL", proc.stderr)
        self.assertNotIn(secret, proc.stdout + proc.stderr)
        self.assertNotIn("config --quiet", calls)

    def test_mismatched_database_password_rejected(self):
        proc, calls = self.run_in_isolated_directory(
            "--cabinet-only", db_url="postgresql://pushok_test:wrong@db:5432/pushok_test")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DATABASE_URL", proc.stderr)
        self.assertNotIn("config --quiet", calls)

    def test_world_readable_env_rejected_before_compose_config(self):
        proc, calls = self.run_in_isolated_directory("--cabinet-only", env_mode=0o644)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("chmod 600", proc.stderr)
        self.assertNotIn("config --quiet", calls)

    def test_enabled_real_stars_billing_rejected_for_staging(self):
        proc, calls = self.run_in_isolated_directory("--with-bot",
            token="123456789:" + "x"*40, username="pushok_testing", stars="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PUSHOK_STARS_PAYMENTS_ENABLED=0", proc.stderr)
        self.assertNotIn("config --quiet", calls)

    def test_bot_mode_rejects_missing_token_before_compose_config(self):
        proc, calls = self.run_in_isolated_directory(
            "--with-bot", username="pushok_testing")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TELEGRAM_BOT_TOKEN", proc.stderr)
        self.assertNotIn("config --quiet", calls)

    def test_bot_mode_checks_profile_with_nonsecret_dummy_token(self):
        proc, calls = self.run_in_isolated_directory(
            "--with-bot", token="123456789:" + "x" * 40,
            username="pushok_testing")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("compose --profile bot config --quiet", calls)
        self.assertNotIn("123456789:", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
