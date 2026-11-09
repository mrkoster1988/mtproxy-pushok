"""v0.038 — безопасные доказательства ручного показа."""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
from app.placement_evidence import sanitize_image, store_image, MAX_BYTES

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000981")
OPERATOR = UUID("00000000-0000-4000-8000-000000000982")
OBS = 19

def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (10, 10), (20, 140, 200)).save(stream, format=fmt)
    return stream.getvalue()

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client = TestClient(main.app)
        self.path = f"/api/v1/operator/campaigns/{CAMPAIGN}/display-observations/{OBS}/evidence"

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()

    def login(self):
        main.app.dependency_overrides[main.require_operator] = lambda: {"id": OPERATOR, "role": "operator"}

    def upload(self, data=None, mime="image/png"):
        return self.client.post(self.path, files={"file": ("proof.png", image_bytes() if data is None else data, mime)})

    def test_requires_operator_before_database(self):
        with patch("app.main.psycopg.connect") as db:
            self.assertEqual(self.upload().status_code, 401)
            self.assertEqual(self.client.get(self.path).status_code, 401)
            db.assert_not_called()

    def test_bad_format_size_and_corruption(self):
        self.login()
        for raw, mime, status in (
            (b"", "image/png", 422),
            (b"not-a-real-png", "image/png", 422),
            (image_bytes(), "application/pdf", 415),
            (image_bytes(), "image/jpeg", 415),
            (b"x" * (MAX_BYTES + 1), "image/png", 413),
        ):
            with self.subTest(size=len(raw), mime=mime), patch("app.main.psycopg.connect") as db:
                result = self.upload(raw, mime)
                self.assertEqual(result.status_code, status, result.text)
                db.assert_not_called()

    def test_cleaned_image_and_private_storage(self):
        class Uploaded:
            file = io.BytesIO(image_bytes("JPEG"))
            content_type = "image/jpeg"
        data, mime, suffix, digest = sanitize_image(Uploaded())
        self.assertEqual((mime, suffix), ("image/jpeg", ".jpg"))
        self.assertEqual(len(digest), 64)
        with Image.open(io.BytesIO(data)) as actual:
            actual.load()
            self.assertEqual(actual.format, "JPEG")
            self.assertFalse(actual.getexif())
        with tempfile.TemporaryDirectory() as directory:
            key, path = store_image(data, suffix, directory)
            self.assertTrue(key.endswith(".jpg"))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)

    def test_insert_author_bound_and_audited(self):
        self.login()
        conn = MagicMock()
        evidence = {"id": UUID("00000000-0000-4000-8000-000000000983"),
                    "observation_id": OBS, "size_bytes": 100, "sha256": "a" * 64,
                    "created_at": datetime.now(timezone.utc)}
        conn.__enter__.return_value.execute.return_value.fetchone.side_effect = [{"id": OBS}, evidence]
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.main.psycopg.connect", return_value=conn), patch.object(main, "PLACEMENT_EVIDENCE_DIR", directory):
                response = self.upload()
                self.assertEqual(len(list(Path(directory).iterdir())), 1)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["telegram_api_verified"])
        calls = conn.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("observer_user_id = %s", calls[0].args[0])
        self.assertEqual(calls[0].args[1], (OBS, CAMPAIGN, OPERATOR))
        self.assertIn("placement_evidence_uploaded", calls[2].args[0])

    def test_another_operator_cannot_attach(self):
        self.login()
        conn = MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.main.psycopg.connect", return_value=conn), patch.object(main, "PLACEMENT_EVIDENCE_DIR", directory):
                response = self.upload()
                self.assertFalse(list(Path(directory).iterdir()))
        self.assertEqual(response.status_code, 404)

    def test_operator_private_download(self):
        self.login()
        conn = MagicMock()
        key = "b" * 32 + ".png"
        import hashlib
        raw = image_bytes()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "storage_key": key, "content_type": "image/png",
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        }
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, key).write_bytes(image_bytes())
            with patch("app.main.psycopg.connect", return_value=conn), patch.object(main, "PLACEMENT_EVIDENCE_DIR", directory):
                response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_migration_ui_and_installer(self):
        self.assertIn("UNIQUE REFERENCES placement_observations", (ROOT / "database/012_placement_evidence.sql").read_text())
        self.assertIn("012_placement_evidence.sql", (ROOT / "setup.sh").read_text())
        js = (ROOT / "api/web/app.js").read_text()
        self.assertIn("Скачать скриншот", js)
        self.assertIn("form.append('file'", js)

if __name__ == "__main__":
    unittest.main()
