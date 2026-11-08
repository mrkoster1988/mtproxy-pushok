"""Русский мастер официального Docker MTProxy — только dry-run и статическая безопасность."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "install_official_docker.sh"
COMPOSE = ROOT / "compose.proxy-official.yaml"


class OfficialDockerInstallerTests(unittest.TestCase):
    def run_plan(self, answers):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(SOURCE, root / "install_official_docker.sh")
            result = subprocess.run(
                ["bash", str(root / "install_official_docker.sh"), "--plan"],
                input=answers, text=True, capture_output=True, timeout=12,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([p.name for p in root.iterdir()],
                             ["install_official_docker.sh"])
            return result.stdout + result.stderr

    def test_plan_defaults_to_local_only_and_never_changes_server(self):
        # host, port, localhost, TAG later, two workers, yes (dry-run)
        result = self.run_plan("\n\n\n\n\n1\n")
        self.assertIn("127.0.0.1:8443", result)
        self.assertIn("mtproxy-pushok-official", result)
        self.assertIn("ничего не изменено", result)

    def test_invalid_port_is_rejected_without_mutation(self):
        result = self.run_plan("\n80000\n19443\n1\n2\n1\n1\n")
        self.assertIn("Допустимый TCP-порт", result)
        self.assertIn("0.0.0.0:19443", result)
        self.assertIn("ничего не изменено", result)

    def test_tag_is_hidden_even_in_plan_output(self):
        tag = "abcdef0123456789abcdef0123456789"
        result = self.run_plan("\n\n2\n1\n" + tag + "\n2\n1\n")
        self.assertIn("TAG: указан, значение скрыто", result)
        self.assertNotIn(tag, result)

    def test_explicit_exit_does_not_write_files(self):
        result = self.run_plan("\n\n\n\n\n2\n")
        self.assertIn("Выйти без изменений", result)
        self.assertNotIn("ничего не изменено", result)

    def test_main_master_offers_both_engines_in_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
            for chosen, expected in (
                ("1\n1\n1\n", "telegrammessenger/proxy:latest"),
                ("1\n2\n1\n", "классический Telegram MTProxy"),
            ):
                with self.subTest(engine=chosen):
                    result = subprocess.run(
                        ["bash", str(root / "setup.sh"), "--plan"],
                        input=chosen, text=True, capture_output=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(expected, result.stdout + result.stderr)
            self.assertEqual([p.name for p in root.iterdir()], ["setup.sh"])

    def test_main_master_defaults_to_safe_auto_classic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
            # Прокси -> Enter (автовыбор) -> Начать, но только в --plan.
            result = subprocess.run(
                ["bash", str(root / "setup.sh"), "--plan"],
                input="1\n\n1\n", text=True,
                capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = result.stdout + result.stderr
            self.assertIn("Автовыбор Pushok: Classic/native", output)
            self.assertIn("изменения не выполнялись", output)
            self.assertEqual([p.name for p in root.iterdir()], ["setup.sh"])

    def test_compose_secrets_are_in_explicit_root_file_not_api_env(self):
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("telegrammessenger/proxy:latest", compose)
        self.assertIn("MTPROXY_SECRET_FILE", compose)
        self.assertIn("MTPROXY_PORT", compose)
        self.assertIn("proxy-data:/data", compose)
        self.assertNotIn("8000:8000", compose)
        self.assertNotIn("/stats", compose)  # stats stays private

    def test_installer_never_destroys_or_reconfigures_existing_proxy(self):
        script = SOURCE.read_text(encoding="utf-8")
        self.assertIn("docker ps -a", script)
        self.assertIn("docker volume ls", script)
        self.assertIn("mtproxy-pushok.service", script)
        self.assertIn("chmod 0600", script)
        self.assertIn("openssl rand -hex 16", script)
        self.assertIn("config --quiet", script)
        self.assertIn("не публикуйте docker logs/inspect", script.lower())
        for unsafe in ("docker compose down", "docker rm", "docker volume rm",
                       "systemctl stop", "systemctl restart", "docker compose --force-recreate",
                       "docker compose config\n"):
            self.assertNotIn(unsafe, script)

    def test_shell_syntax(self):
        for path in (SOURCE, ROOT / "setup.sh"):
            result = subprocess.run(["bash", "-n", str(path)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
