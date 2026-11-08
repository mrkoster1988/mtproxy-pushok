"""v0.026: upstream provenance, secure offline config and scripts without VPS changes."""
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from app.pushok_core_config import render_staging_config, validate_domain


class PushokCoreTests(unittest.TestCase):
    def test_render_toml_is_loopback_and_api_off_by_default(self):
        source = render_staging_config(
            tls_domain="PROXY.EXAMPLE.ORG", port=18443,
            secret="a" * 32, username="pushok",
        )
        doc = tomllib.loads(source)
        self.assertTrue(doc["general"]["use_middle_proxy"])
        self.assertEqual(doc["server"]["port"], 18443)
        self.assertFalse(doc["server"]["api"]["enabled"])
        self.assertEqual(doc["server"]["listeners"][0]["ip"], "127.0.0.1")
        self.assertEqual(doc["censorship"]["tls_domain"], "proxy.example.org")
        self.assertEqual(doc["access"]["users"]["pushok"], "a" * 32)
        self.assertNotIn("ad_tag", doc["general"])

    def test_real_nonzero_ad_tag_in_optional_preview(self):
        doc = tomllib.loads(render_staging_config(
            tls_domain="proxy.example.org", secret="a" * 32, ad_tag="b" * 32
        ))
        self.assertEqual(doc["general"]["ad_tag"], "b" * 32)

    def test_random_secret_has_correct_shape(self):
        doc = tomllib.loads(render_staging_config(tls_domain="proxy.example.org"))
        secret = doc["access"]["users"]["pushok"]
        self.assertEqual(len(secret), 32)
        self.assertNotEqual(secret, "0" * 32)
        self.assertEqual(set(secret) - set("0123456789abcdef"), set())

    def test_inputs_reject_injection_paths_and_unsafe_values(self):
        for bad in (
            "https://t.me/proxy", "proxy.org:443", "a..example.org",
            "127.0.0.1", "proxy.example.org/path", 'a.example.com"\n[evil]',
            "localhost", "*.example.org", "bad example.org",
        ):
            with self.subTest(domain=bad), self.assertRaises(ValueError):
                validate_domain(bad)
        for kwargs in (
            {"port": 0}, {"port": 65536}, {"port": True},
            {"username": "name"\n[evil]"},
            {"username": "bad username"},
            {"secret": "0" * 32},
            {"secret": "a" * 31},
            {"ad_tag": "0" * 32},
            {"ad_tag": "a" * 31},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                render_staging_config(tls_domain="proxy.example.org", **kwargs)

    def test_upstream_pin_is_specific_and_license_retained(self):
        lock = (ROOT / "third_party/telemt/UPSTREAM.lock").read_text()
        license_text = (ROOT / "third_party/telemt/LICENSE").read_text()
        notice = (ROOT / "third_party/telemt/NOTICE.md").read_text()
        self.assertIn("tag=3.5.17", lock)
        self.assertIn("commit=66708d2866f3f3a4541c4fa67984d408e71a8af4", lock)
        self.assertIn("TELEMT LICENSE 3.3", license_text)
        self.assertIn("Copyright (c) 2026 Telemt", license_text)
        self.assertIn("source-fetch/build workflow", notice)

    def test_scripts_plan_does_not_change_any_files(self):
        for path in ("scripts/fetch_pushok_core.sh", "scripts/build_pushok_core.sh"):
            with self.subTest(path=path):
                source = ROOT / path
                result = subprocess.run(["bash", "-n", str(source)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run(["bash", str(source), "--plan"],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ПЛАН:", result.stdout)
        self.assertIn("--locked", (ROOT / "scripts/build_pushok_core.sh").read_text())
        self.assertIn("git -C \"$TMP\" rev-parse HEAD",
                      (ROOT / "scripts/fetch_pushok_core.sh").read_text())

    def test_prepare_plan_does_not_print_secret_or_create_config(self):
        output = ROOT / ".build/pushok-core/staging.toml"
        before = output.exists()
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/prepare_pushok_core.py"),
            "--tls-domain", "proxy.example.org", "--port", "8443",
        ], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("127.0.0.1:8443", result.stdout)
        self.assertIn("API отключён", result.stdout)
        self.assertEqual(output.exists(), before)

    def test_unified_setup_core_plan_is_read_only(self):
        result = subprocess.run(["bash", str(ROOT / "setup.sh"), "--core-plan"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Source Core", result.stdout + result.stderr)
        self.assertIn("ПЛАН:", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
