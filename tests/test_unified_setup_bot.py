"""Тесты единого сценария установки Telegram-бота без Docker и реального токена."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "setup.sh"


class UnifiedBotInstallerTests(unittest.TestCase):
    def run_plan(self, answers, existing_env=None, status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(SOURCE, root / "setup.sh")
            if existing_env is not None:
                (root / ".env").write_text(existing_env, encoding="utf-8")
            result = subprocess.run(
                ["bash", str(root / "setup.sh"), "--plan"],
                input=answers, text=True, capture_output=True, timeout=12,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, status, output)
            self.assertEqual(
                {p.name for p in root.iterdir()},
                {"setup.sh", ".env"} if existing_env is not None else {"setup.sh"},
            )
            if existing_env is not None:
                self.assertEqual((root / ".env").read_text(encoding="utf-8"), existing_env)
            return output

    def test_new_install_with_telegram_starts_bot_in_plan(self):
        token = "123456:Test-Private_Token"
        # cabinet, private staging, default project/port, telegram now,
        # token, username, owner later, payment later, confirm
        answers = "2\n2\n\n\n1\n" + token + "\nmtproxy_pushok_bot\n2\n2\n1\n"
        output = self.run_plan(answers)
        self.assertIn("запуск polling вместе с кабинетом", output)
        self.assertIn("Режим --plan: изменения не выполнялись", output)
        self.assertNotIn(token, output)

    def test_new_install_without_telegram_does_not_start_bot(self):
        output = self.run_plan("2\n2\n\n\n2\n2\n1\n")
        self.assertIn("новый запуск не запланирован", output)

    def test_existing_install_opt_in_requires_valid_credentials(self):
        invalid = "TELEGRAM_BOT_TOKEN=\nTELEGRAM_BOT_USERNAME=\n"
        output = self.run_plan("2\n4\n", existing_env=invalid, status=1)
        self.assertIn("Для запуска бота в .env нужны", output)

    def test_existing_install_opt_in_keeps_secrets_and_files(self):
        token = "123456:SomethingSecret"
        content = (
            "TELEGRAM_BOT_TOKEN=" + token + "\n"
            "TELEGRAM_BOT_USERNAME=mtproxy_pushok_bot\n"
            "BOT_INTERNAL_SECRET=" + "a" * 64 + "\n"
        )
        output = self.run_plan("2\n4\n1\n", existing_env=content)
        self.assertIn("запуск polling вместе с кабинетом", output)
        self.assertNotIn(token, output)
        self.assertNotIn("a" * 64, output)

    def test_only_one_compose_project_for_cabinet_and_bot(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("docker compose --profile bot config --quiet", source)
        self.assertIn("docker compose --profile bot up --build -d", source)
        self.assertIn("docker compose --profile bot ps -q --status running bot", source)
        self.assertNotIn("docker compose down -v", source)

    def test_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SOURCE)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
