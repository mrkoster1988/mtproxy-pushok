"""Безопасная проверка русскоязычного мастера: только --plan в пустом каталоге."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "setup.sh"


class SetupPlanTests(unittest.TestCase):
    def invoke(self, answers, existing_env=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(SOURCE, root / "setup.sh")
            if existing_env is not None:
                (root / ".env").write_text(existing_env)
            result = subprocess.run(
                ["bash", str(root / "setup.sh"), "--plan"],
                input=answers, text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected_files = {"setup.sh", ".env"} if existing_env is not None else {"setup.sh"}
            self.assertEqual({p.name for p in root.iterdir()}, expected_files)
            if existing_env is not None:
                self.assertEqual((root / ".env").read_text(), existing_env)
            return result.stdout + result.stderr

    def test_private_staging_defaults_without_changes(self):
        text = self.invoke("2\n2\n\n\n2\n2\n1\n")
        self.assertIn("mtproxy-pushok-staging", text)
        self.assertIn("8010", text)
        self.assertIn("изменения не выполнялись", text)

    def test_invalid_project_and_port_are_retried(self):
        text = self.invoke("9\n2\n2\n../wrong\nsmoke-staging\n0\n8019\n2\n2\n1\n")
        self.assertIn("Введите номер из списка", text)
        self.assertIn("Порт должен быть", text)
        self.assertIn("smoke-staging", text)
        self.assertIn("8019", text)

    def test_existing_environment_is_preserved(self):
        self.invoke("2\n1\n2\n", existing_env="SESSION_SIGNING_KEY=test-only\n")
