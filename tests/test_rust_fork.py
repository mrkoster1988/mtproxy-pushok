"""Pushok v0.027: patch реально изменяет Rust-исходник и безопасен до запуска."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "third_party/telemt/patches/0001-pushok-build-info.patch"
SRC = ROOT / "scripts/apply_pushok_core_patch.sh"
ORIGINAL = """//! telemt — Telegram MTProto Proxy

mod api;
mod cli;
mod config;
mod conntrack_control;
mod crypto;
#[cfg(unix)]
mod daemon;
mod error;
mod healthcheck;
mod ip_tracker;
#[cfg(test)]
#[path = "tests/ip_tracker_encapsulation_adversarial_tests.rs"]
mod ip_tracker_encapsulation_adversarial_tests;
#[cfg(test)]
#[path = "tests/ip_tracker_hotpath_adversarial_tests.rs"]
mod ip_tracker_hotpath_adversarial_tests;
#[cfg(test)]
#[path = "tests/ip_tracker_regression_tests.rs"]
mod ip_tracker_regression_tests;
mod logging;
mod maestro;
mod metrics;
mod network;
mod protocol;
mod proxy;
mod quota_state;
mod service;
mod slot_budget;
mod startup;
mod stats;
mod stream;
mod synlimit_control;
mod tls_front;
mod transport;
mod util;
mod web;

fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    // Install rustls crypto provider early
    let _ = rustls::crypto::ring::default_provider().install_default();

    let args: Vec<String> = std::env::args().skip(1).collect();
    let cmd = cli::parse_command(&args);

    // Handle subcommands that don't need the server (stop, reload, status, init)
    if let Some(exit_code) = cli::execute_subcommand(&cmd) {
        std::process::exit(exit_code);
    }
"""


class RustForkPatchTests(unittest.TestCase):
    def test_source_patch_has_only_expected_paths(self):
        patch = PATCH.read_text(encoding="utf-8")
        self.assertEqual(
            [line for line in patch.splitlines() if line.startswith("diff --git ")],
            ["diff --git a/src/main.rs b/src/main.rs",
             "diff --git a/src/pushok_build.rs b/src/pushok_build.rs"],
        )
        self.assertNotIn("systemctl", patch)
        self.assertNotIn("cargo run", patch)
        self.assertIn("--pushok-build-info", patch)
        self.assertIn("proxy_started", patch)

    def test_patch_applies_and_reverses_against_pinned_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/main.rs").write_text(ORIGINAL, encoding="utf-8")
            shutil.copy2(PATCH, root / "test.patch")
            for args in (["apply", "--check", "test.patch"],
                         ["apply", "test.patch"]):
                result = subprocess.run(["git", *args], cwd=root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            changed = (root / "src/main.rs").read_text()
            new_module = (root / "src/pushok_build.rs").read_text()
            self.assertIn("mod pushok_build;", changed)
            self.assertIn("return Ok(())", changed)
            self.assertIn("BUILD_INFO_JSON", new_module)
            start = new_module.index('r#"') + 3
            end = new_module.index('"#;', start)
            identity = json.loads(new_module[start:end])
            self.assertEqual(identity["product"], "Pushok Source Core")
            self.assertEqual(identity["version"], "0.027")
            self.assertFalse(identity["proxy_started"])
            result = subprocess.run(["git", "apply", "-R", "--check", "test.patch"],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_patch_plan_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(SRC, root / "apply_pushok_core_patch.sh")
            # Plan needs the fixed patch; copy project layout without executing anything.
            patchdir = root / "third_party/telemt/patches"
            patchdir.mkdir(parents=True)
            shutil.copy2(PATCH, patchdir / PATCH.name)
            result = subprocess.run(["bash", str(root / "apply_pushok_core_patch.sh"),
                                     "--plan"], input="", text=True,
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ПЛАН:", result.stdout)
            self.assertEqual(
                {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()},
                {"apply_pushok_core_patch.sh",
                 "third_party/telemt/patches/0001-pushok-build-info.patch"},
            )

    def test_shell_and_build_script_require_patch(self):
        for name in ("scripts/build_pushok_core.sh",
                     "scripts/apply_pushok_core_patch.sh"):
            file = ROOT / name
            result = subprocess.run(["bash", "-n", str(file)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        build = (ROOT / "scripts/build_pushok_core.sh").read_text()
        self.assertIn("apply_pushok_core_patch.sh", build)
        self.assertIn("cargo build --release --locked", build)
        self.assertIn('x["patch"] == "cli-build-info-001"', build)
        self.assertNotIn("systemctl start", build)

    def test_license_and_modifications_manifest(self):
        notice = (ROOT / "third_party/telemt/NOTICE.md").read_text()
        changes = (ROOT / "third_party/telemt/PATCHES.md").read_text()
        license_text = (ROOT / "third_party/telemt/LICENSE").read_text()
        self.assertIn("v0.027", changes)
        self.assertIn("patches/0001-pushok-build-info.patch", changes)
        self.assertIn("TELEMT LICENSE 3.3", license_text)
        self.assertIn("modified", notice.lower())


if __name__ == "__main__":
    unittest.main()
