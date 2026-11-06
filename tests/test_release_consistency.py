"""Проверки согласованности версии между API, установщиком и кабинетом."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseConsistencyTests(unittest.TestCase):
    def test_version_is_consistent(self):
        api_source = (ROOT / "api/app/main.py").read_text(encoding="utf-8")
        match = re.search(r'\bversion="(\d+\.\d+)"', api_source)
        self.assertIsNotNone(match, "В FastAPI отсутствует номер версии")
        version = match.group(1)
        self.assertIn(f"v{version}", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn(f"v{version}", (ROOT / "setup.sh").read_text(encoding="utf-8"))
        self.assertIn(f"v{version}", (ROOT / "api/web/index.html").read_text(encoding="utf-8"))

    def test_mvp_status_not_stale(self):
        mvp = (ROOT / "docs/MVP.md").read_text(encoding="utf-8")
        self.assertNotIn("реализация API и кабинета ещё не начата", mvp)


if __name__ == "__main__":
    unittest.main()
