"""v0.039 — закрытый аудит, SHA256, запрет подмены symlink."""
import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from PIL import Image
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY", "a" * 64)
from app import main
from app.placement_evidence import EvidenceReadError, read_verified_image

NAME = "a" * 32 + ".png"
CID = UUID("00000000-0000-4000-8000-000000000991")
UID = UUID("00000000-0000-4000-8000-000000000992")
OBS = 41

def png():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), (2, 120, 180)).save(stream, format="PNG")
    return stream.getvalue()

class EvidenceIntegrityTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def test_checksum_size_and_path_validation(self):
        data = png()
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, NAME).write_bytes(data)
            self.assertEqual(read_verified_image(folder, NAME, digest, len(data), "image/png"), data)
            for sha, size, mime, expected in (
                ("0" * 64, len(data), "image/png", "evidence_integrity_failed"),
                (digest, len(data) + 1, "image/png", "evidence_integrity_failed"),
                (digest, len(data), "image/jpeg", "evidence_invalid_metadata"),
            ):
                with self.subTest(expected=expected), self.assertRaisesRegex(EvidenceReadError, expected):
                    read_verified_image(folder, NAME, sha, size, mime)
            with self.assertRaisesRegex(EvidenceReadError, "evidence_invalid_metadata"):
                read_verified_image(folder, "../" + NAME, digest, len(data), "image/png")

    def test_missing_and_symlink(self):
        data = png()
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, "source.png")
            target.write_bytes(data)
            link = Path(folder, NAME)
            link.symlink_to(target)
            with self.assertRaisesRegex(EvidenceReadError, "evidence_storage_unavailable"):
                read_verified_image(folder, NAME, digest, len(data), "image/png")
            link.unlink()
            with self.assertRaisesRegex(EvidenceReadError, "evidence_file_missing"):
                read_verified_image(folder, NAME, digest, len(data), "image/png")

    def test_admin_auth_required_without_sql(self):
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get("/api/v1/admin/placement-evidence/integrity")
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()

    def test_admin_audit_is_read_only_and_reports_missing(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": UID, "role": "admin"}
        data = png()
        good = {
            "id": CID, "observation_id": OBS, "storage_key": NAME,
            "content_type": "image/png", "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        bad = {**good, "id": UID, "observation_id": OBS + 1, "storage_key": "b" * 32 + ".png"}
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchall.return_value = [good, bad]
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, NAME).write_bytes(data)
            with patch("app.main.psycopg.connect", return_value=conn), patch.object(main, "PLACEMENT_EVIDENCE_DIR", folder):
                result = self.client.get("/api/v1/admin/placement-evidence/integrity?limit=2")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()["checked"], result.json()["healthy"]), (2, 1))
        self.assertEqual(result.json()["issues"][0]["issue"], "evidence_file_missing")
        self.assertFalse(result.json()["changes_applied"])
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].args[0].strip().startswith("SELECT"))
        self.assertEqual(calls[0].args[1], (2,))

    def test_admin_limit_rejected_before_sql(self):
        main.app.dependency_overrides[main.require_admin] = lambda: {"id": UID, "role": "admin"}
        with patch("app.main.psycopg.connect") as db:
            response = self.client.get("/api/v1/admin/placement-evidence/integrity?limit=101")
        self.assertEqual(response.status_code, 422)
        db.assert_not_called()

    def test_download_rejects_tampered_file(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {"id": UID, "role": "operator"}
        data = png()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "storage_key": NAME, "content_type": "image/png",
            "size_bytes": len(data), "sha256": "0" * 64,
        }
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, NAME).write_bytes(data)
            with patch("app.main.psycopg.connect", return_value=conn), patch.object(main, "PLACEMENT_EVIDENCE_DIR", folder):
                result = self.client.get(f"/api/v1/operator/campaigns/{CID}/display-observations/{OBS}/evidence")
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"], "evidence_integrity_failed")

if __name__ == "__main__":
    unittest.main()
