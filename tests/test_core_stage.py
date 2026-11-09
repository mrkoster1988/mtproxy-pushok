"""v0.029: offline attestation and atomic TEST marker, no live proxy interactions."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)

from scripts import core_stage
from app import main
from app.core_readiness import core_readiness


class LocalCoreStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / ".build" / "pushok-core"
        self.out = self.stage / "output"
        self.out.mkdir(parents=True)
        self.core = self.out / core_stage.NAME
        self.checksum = self.out / (core_stage.NAME + ".sha256")
        upstream = self.root / "third_party" / "telemt"
        upstream.mkdir(parents=True)
        (upstream / "LICENSE").write_text("TEST LICENSE", encoding="utf-8")
        for path in ("TELEMT-LICENSE", "NOTICE.md", "PATCHES.md"):
            (self.out / path).write_text("TEST LICENSE" if path == "TELEMT-LICENSE"
                                         else "TEST NOTICE", encoding="utf-8")
        # The dummy CLI only prints a fixed JSON string. No sockets or filesystem writes.
        identity = json.dumps(core_stage.EXPECTED, separators=(",", ":"))
        self.core.write_text("#!/bin/sh\nprintf '%s\\n' '" + identity + "'\n", encoding="utf-8")
        self.core.chmod(0o700)
        self.checksum.write_text(
            hashlib.sha256(self.core.read_bytes()).hexdigest() + "  " + core_stage.NAME + "\n",
            encoding="ascii",
        )
        patches = [
            patch.object(core_stage, "ROOT", self.root),
            patch.object(core_stage, "STAGE", self.stage),
            patch.object(core_stage, "OUTPUT", self.out),
            patch.object(core_stage, "BINARY", self.core),
            patch.object(core_stage, "CHECKSUM", self.checksum),
            patch.object(core_stage, "STATE", self.stage / "staging-state.json"),
            patch.object(core_stage, "LOCK", self.stage / ".staging-state.lock"),
            patch.object(core_stage.os, "geteuid", return_value=1001),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_offline_identity_verified_without_starting_proxy(self):
        result = core_stage.inspect()
        self.assertEqual(result["status"], "verified_offline")
        self.assertTrue(result["checksum_verified"])
        self.assertTrue(result["license_verified"])
        self.assertFalse(result["runtime_started"])
        self.assertFalse(result["telegram_tested"])
        self.assertFalse(result["sponsor_tested"])

    def test_tamper_is_rejected_before_any_execution(self):
        self.core.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        with patch.object(core_stage.subprocess, "Popen",
                          side_effect=AssertionError("unverified binary executed")) as runner:
            with self.assertRaisesRegex(core_stage.StageError, "core_checksum_mismatch"):
                core_stage.inspect()
            runner.assert_not_called()

    def test_missing_license_or_artifact_fails_closed(self):
        (self.out / "TELEMT-LICENSE").write_text("CHANGED")
        with self.assertRaisesRegex(core_stage.StageError, "core_license_mismatch"):
            core_stage.inspect()
        self.core.unlink()
        with self.assertRaisesRegex(core_stage.StageError, "built_core_artifact_missing"):
            core_stage.inspect()

    def test_symlink_binary_is_rejected(self):
        self.core.unlink()
        target = self.out / "untrusted"
        target.write_text("untrusted")
        self.core.symlink_to(target)
        with self.assertRaisesRegex(core_stage.StageError, "staging_symlink_forbidden"):
            core_stage.inspect()

    def test_staging_marker_can_be_activated_and_rolled_back(self):
        with patch.object(sys, "argv", ["core_stage.py", "--activate"]):
            self.assertEqual(core_stage.main(), 0)
        state = core_stage.load_state()
        self.assertEqual(state["active"]["sha256"],
                         hashlib.sha256(self.core.read_bytes()).hexdigest())
        self.assertIsNone(state["previous"])
        self.assertEqual(core_stage.STATE.stat().st_mode & 0o777, 0o600)

        # Repeating activation is idempotent and does not rewrite the marker.
        mtime = core_stage.STATE.stat().st_mtime_ns
        with patch.object(sys, "argv", ["core_stage.py", "--activate"]):
            self.assertEqual(core_stage.main(), 0)
        self.assertEqual(core_stage.STATE.stat().st_mtime_ns, mtime)

        with patch.object(sys, "argv", ["core_stage.py", "--rollback"]):
            self.assertEqual(core_stage.main(), 0)
        self.assertIsNone(core_stage.load_state()["active"])
        with patch.object(sys, "argv", ["core_stage.py", "--rollback"]):
            self.assertEqual(core_stage.main(), 2)

    def test_plan_has_no_disk_writes(self):
        with patch.object(sys, "argv", ["core_stage.py", "--plan"]):
            self.assertEqual(core_stage.main(), 0)
        self.assertFalse(core_stage.STATE.exists())
        self.assertFalse(core_stage.LOCK.exists())

    def test_invalid_state_is_never_overwritten_by_activate(self):
        core_stage.STATE.write_text('{"schema_version":1,"active":"/etc/passwd"}')
        with patch.object(sys, "argv", ["core_stage.py", "--activate"]):
            self.assertEqual(core_stage.main(), 2)
        self.assertIn("/etc/passwd", core_stage.STATE.read_text())

    def test_script_never_calls_service_manager_or_telegram(self):
        source = (ROOT / "scripts/core_stage.py").read_text(encoding="utf-8")
        for unsafe in ("subprocess.run([\"systemctl\"", "docker compose up",
                       "requests.post(", "TelegramBot", "httpx.post("):
            self.assertNotIn(unsafe, source)


class CoreReadinessAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def test_readiness_is_explicitly_not_a_live_health_check(self):
        report = core_readiness()
        self.assertEqual(report["project_version"], "0.029")
        self.assertEqual(report["core_version"], "0.027")
        self.assertIsNone(report["local_build_verified"])
        self.assertEqual(report["runtime_status"], "not_checked")
        self.assertFalse(report["telegram_connection_verified"])
        self.assertFalse(report["sponsor_verified"])
        self.assertFalse(report["changes_applied"])

    def test_non_admin_rejected_and_admin_route_never_touches_host(self):
        with patch("app.main.psycopg.connect") as db, \
                patch("app.main.subprocess.Popen", create=True) as proc:
            response = self.client.get("/api/v1/admin/pushok-core/readiness")
            self.assertEqual(response.status_code, 401)
            main.app.dependency_overrides[main.require_admin] = lambda: {
                "id": "00000000-0000-4000-8000-000000000029", "role": "admin"
            }
            response = self.client.get("/api/v1/admin/pushok-core/readiness")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["runtime_status"], "not_checked")
            self.assertFalse(response.json()["changes_applied"])
            db.assert_not_called()
            proc.assert_not_called()

    def test_admin_cabinet_keeps_readiness_display(self):
        html = (ROOT / "api/web/index.html").read_text()
        js = (ROOT / "api/web/app.js").read_text()
        self.assertIn('id="core-readiness"', html)
        self.assertIn("admin/pushok-core/readiness", js)
        self.assertIn("textContent=message", js)
        self.assertIn("не проверялась", js)


if __name__ == "__main__":
    unittest.main()
