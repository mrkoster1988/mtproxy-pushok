"""Тесты preflight без доступа к реальной базе, сети и секретам."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pushok_preflight", ROOT / "scripts" / "preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def fixture(root: Path) -> None:
    files = {
        "README.md": "Версия проекта: v0.042.\n",
        "api/app/main.py": 'app = FastAPI(version="0.042")\n"/api/v1/campaigns/{campaign_id}/report"\n"/api/v1/campaigns/{campaign_id}/report.csv"\n',
        "api/app/core_readiness.py": '{"project_version": "0.042", "project_version": "0.042"}\n',
        "api/web/index.html": "v0.042</span></footer>\n",
        "setup.sh": "MTProxy Pushok v0.042\ndatabase/002_second.sql\ndatabase/003_third.sql\n",
        "CHANGELOG.md": "## [0.042] — test\n",
        "database/001_first.sql": "SELECT 1;\n",
        "database/002_second.sql": "SELECT 2;\n",
        "database/003_third.sql": "SELECT 3;\n",
        ".github/workflows/check.yml": "python3 scripts/preflight.py\npython -m unittest discover -s tests -v\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture(self.root)

    def failed_checks(self):
        return {item["check"] for item in preflight.inspect(self.root)["checks"] if not item["passed"]}

    def test_all_checks_pass_on_consistent_fixture(self):
        report = preflight.inspect(self.root)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["passed"], report["total"])
        self.assertFalse(report["ci_executed"])

    def test_version_mismatch_detected(self):
        p = self.root / "api/app/main.py"
        p.write_text(p.read_text().replace('version="0.042"', 'version="0.041"'))
        self.assertIn("api_version", self.failed_checks())

    def test_migration_gap_detected(self):
        (self.root / "database/002_second.sql").unlink()
        self.assertIn("migrations_contiguous", self.failed_checks())

    def test_setup_missing_migration_detected(self):
        p = self.root / "setup.sh"
        p.write_text(p.read_text().replace("database/003_third.sql", ""))
        self.assertIn("installer_migrations", self.failed_checks())

    def test_ci_suite_removal_detected(self):
        p = self.root / ".github/workflows/check.yml"
        p.write_text("python3 scripts/preflight.py\n")
        self.assertIn("github_test_suite", self.failed_checks())

    def test_legacy_ci_configuration_rejected(self):
        (self.root / ".gitlab-ci.yml").write_text(
            "test: python -m unittest discover -s tests -v\n", encoding="utf-8")
        self.assertIn("github_only_ci", self.failed_checks())

    def test_github_preflight_removal_detected(self):
        p = self.root / ".github/workflows/check.yml"
        p.write_text("python -m unittest discover -s tests -v\n")
        self.assertIn("github_preflight", self.failed_checks())

    def test_cli_json_reports_errors_and_exits_nonzero(self):
        p = self.root / "api/web/index.html"
        p.write_text("wrong version")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/preflight.py"), "--root", str(self.root), "--json"],
            check=False, text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("cabinet_version", {x["check"] for x in payload["checks"] if not x["passed"]})


if __name__ == "__main__":
    unittest.main()
