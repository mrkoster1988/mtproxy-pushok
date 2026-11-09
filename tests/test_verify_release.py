"""Автономные тесты оркестратора проверки, без доступа к VPS и реальной БД."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_release", ROOT / "scripts/verify_release.py")
verify_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_release)


def fixture(root):
    files = {
        "scripts/preflight.py": 'import json\nprint(json.dumps({"ok":True}))\n',
        "scripts/verify_release.py": (ROOT / "scripts/verify_release.py").read_text(),
        "tests/test_preflight.py": 'import unittest\nclass T(unittest.TestCase):\n def test_good(self): self.assertTrue(True)\n',
        "api/app/main.py": 'version="0.043"\n',
        "bot/main.py": 'value=1\n',
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class VerifyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root)

    def test_offline_pass_does_not_accept_release(self):
        report = verify_release.verify(self.root, env={})
        self.assertTrue(report["checks_passed"], report)
        self.assertFalse(report["release_accepted"])
        self.assertFalse(report["ci_verified"])
        self.assertFalse(report["vps_verified"])

    def test_syntax_failure_caught(self):
        (self.root / "bot/main.py").write_text("def broken(\n")
        report = verify_release.verify(self.root, env={})
        self.assertFalse(report["checks_passed"])
        self.assertFalse(report["checks"][0]["passed"])

    def test_preflight_error_caught(self):
        (self.root / "scripts/preflight.py").write_text("raise SystemExit(1)\n")
        report = verify_release.verify(self.root, env={})
        self.assertFalse(next(x for x in report["checks"] if x["name"] == "repository_preflight")["passed"])

    def test_full_requires_opt_in(self):
        (self.root / "tests/test_postgres.py").write_text("import unittest\n")
        report = verify_release.verify(self.root, "full", env={"TEST_ADMIN_DSN": "postgresql://u:p@localhost/postgres"})
        self.assertFalse(report["checks_passed"])
        self.assertFalse(next(x for x in report["checks"] if x["name"] == "isolated_test_database")["passed"])

    def test_remote_database_rejected(self):
        self.assertFalse(verify_release.allowed_test_dsn("postgresql://u:p@prod.example.com/live"))
        self.assertFalse(verify_release.allowed_test_dsn("postgresql://u:p@127.0.0.1/"))
        self.assertFalse(verify_release.allowed_test_dsn("postgresql://u:p@postgres/postgres"))
        self.assertTrue(verify_release.allowed_test_dsn("postgresql://u:p@localhost/postgres"))
        self.assertTrue(verify_release.allowed_test_dsn("postgresql://u:p@postgres/postgres", ci=True))

    def test_full_does_not_inherit_production_db_url(self):
        (self.root / "tests/test_postgres.py").write_text("import unittest\n")
        calls = []
        def fake(cmd, *, root, env, timeout=900):
            calls.append((cmd, dict(env)))
            return {"name": "cmd", "passed": True, "detail": "ok"}
        with patch.object(verify_release, "run_command", side_effect=fake):
            report = verify_release.verify(self.root, "full", allow_test_db=True,
                env={"TEST_ADMIN_DSN": "postgresql://u:p@localhost/postgres",
                     "DATABASE_URL": "postgresql://prod.example.com/live"})
        self.assertTrue(report["checks_passed"], report)
        self.assertEqual(calls[-1][1]["DATABASE_URL"], "postgresql://u:p@localhost/postgres")
        self.assertNotIn("DATABASE_URL", calls[0][1])

    def test_full_requires_postgres_test_module(self):
        report = verify_release.verify(self.root, "full", allow_test_db=True,
            env={"TEST_ADMIN_DSN": "postgresql://u:p@localhost/postgres"})
        self.assertFalse(report["checks_passed"])
        self.assertFalse(next(x for x in report["checks"] if x["name"] == "postgres_integration_suite_present")["passed"])

    def test_full_fixture_tests_do_not_claim_release_acceptance(self):
        (self.root / "tests/test_postgres.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_fake(self): self.assertTrue(True)\n")
        report = verify_release.verify(self.root, "full", allow_test_db=True,
                                       env={"TEST_ADMIN_DSN": "postgresql://u:p@localhost/postgres"})
        self.assertTrue(report["checks_passed"], report)
        self.assertFalse(report["release_accepted"])
        self.assertEqual(report["checks"][-1]["name"], "all_python_tests_with_postgresql")


if __name__ == "__main__":
    unittest.main()
