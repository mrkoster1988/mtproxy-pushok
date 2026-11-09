"""v0.030: no-network real TCP smoke planning and fail-closed cleanup."""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
from app.pushok_core_config import render_staging_config
from scripts import core_stage, core_tcp_smoke


class IsolatedTCPTests(unittest.TestCase):
    def test_config_is_loopback_only_and_never_registers_sponsor(self):
        config = core_tcp_smoke.safe_config()
        parsed = tomllib.loads(config)
        self.assertEqual(parsed["server"]["port"], 18443)
        self.assertEqual(parsed["server"]["listeners"], [{"ip": "127.0.0.1"}])
        self.assertFalse(parsed["server"]["api"]["enabled"])
        self.assertFalse(parsed["general"]["use_middle_proxy"])
        self.assertFalse(parsed["censorship"]["mask"])
        self.assertFalse(parsed["censorship"]["tls_emulation"])
        self.assertNotIn("ad_tag", parsed["general"])
        self.assertEqual(len(parsed["access"]["users"]["pushok"]), 32)
        self.assertNotIn("00000000000000000000000000000000", config)

    def test_normal_config_is_unchanged_and_ad_tag_blocked_in_smoke(self):
        normal = tomllib.loads(render_staging_config(
            tls_domain="proxy.example.org", secret="1"*32
        ))
        self.assertTrue(normal["general"]["use_middle_proxy"])
        self.assertTrue(normal["censorship"]["mask"])
        with self.assertRaisesRegex(ValueError, "ad_tag_forbidden_in_offline_smoke"):
            render_staging_config(tls_domain="proxy.example.org",
                                  offline_smoke=True, ad_tag="a" * 32)
        with self.assertRaisesRegex(ValueError, "invalid_offline_smoke"):
            render_staging_config(tls_domain="proxy.example.org", offline_smoke=1)

    def test_docker_flags_are_private_low_privilege_and_bounded(self):
        cmd = core_tcp_smoke.docker_command(
            probe_dir=Path("/tmp/probe-123"),
            name="pushok-core-smoke-abcdef123456",
        )
        self.assertEqual(cmd[:2], ["docker", "run"])
        self.assertIn("none", cmd)
        self.assertEqual(cmd[cmd.index("--network")+1], "none")
        self.assertEqual(cmd[cmd.index("--pull")+1], "never")
        self.assertEqual(cmd[cmd.index("--cap-drop")+1], "ALL")
        self.assertEqual(cmd[cmd.index("--security-opt")+1], "no-new-privileges")
        self.assertIn("--read-only", cmd)
        self.assertNotIn("-p", cmd)
        self.assertNotIn("--publish", cmd)
        self.assertEqual(cmd[-3], "bash")
        self.assertIn("/dev/tcp/127.0.0.1/18443", cmd[-1])
        self.assertIn("trap cleanup EXIT", cmd[-1])
        with self.assertRaises(core_tcp_smoke.ProbeError):
            core_tcp_smoke.docker_command(probe_dir=Path("/tmp/x"),
                                          name="other-container")

    def test_plan_has_no_side_effects_and_requires_no_docker(self):
        with patch.object(sys, "argv", ["core_tcp_smoke.py", "--plan"]), \
             patch.object(core_tcp_smoke.subprocess, "run",
                          side_effect=AssertionError("docker unexpectedly invoked")):
            self.assertEqual(core_tcp_smoke.main(), 0)

    def test_isolated_probe_verifies_artifact_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            fake_binary = stage / "pushok-core-pinned"
            fake_binary.write_bytes(b"not executed in these unit tests")
            calls = []
            config_contents = []
            def fake_docker(cmd, **kwargs):
                calls.append(cmd)
                if cmd[:3] == ["docker", "image", "inspect"]:
                    return subprocess.CompletedProcess(cmd, 0, b"", b"")
                if cmd[:2] == ["docker", "run"]:
                    mount = cmd[cmd.index("--mount")+2]
                    config_dir = Path(mount.split("source=")[1].split(",target=")[0])
                    config = config_dir / "probe.toml"
                    self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                    config_contents.append(config.read_text())
                    return subprocess.CompletedProcess(
                        cmd, 0, b"pushok_core_loopback_tcp_ready\n", b"")
                if cmd[:3] == ["docker", "rm", "--force"]:
                    return subprocess.CompletedProcess(
                        cmd, 1, b"", b"Error: No such container")
                raise AssertionError("unexpected docker command")
            with patch.object(core_stage, "STAGE", stage), \
                 patch.object(core_stage, "BINARY", fake_binary), \
                 patch.object(core_stage, "inspect",
                              return_value={"status": "verified_offline"}), \
                 patch.object(core_tcp_smoke.subprocess, "run",
                              side_effect=fake_docker):
                report = core_tcp_smoke.run_probe()
            self.assertTrue(report["container_removed"])
            self.assertFalse(report["telegram_verified"])
            self.assertFalse(report["mtproxy_handshake_verified"])
            self.assertFalse(report["host_runtime_changed"])
            self.assertEqual(len(config_contents), 1)
            self.assertFalse((stage / "probe.toml").exists())
            self.assertEqual(len(list(stage.glob(".loopback-probe.*"))), 0)
            self.assertEqual(calls[-1][:3], ["docker", "rm", "--force"])

    def test_failed_or_timed_out_container_is_always_removed(self):
        for failure in ("failed", "timeout"):
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory() as directory:
                    stage = Path(directory)
                    binary = stage / "placeholder"
                    binary.write_bytes(b"x")
                    calls = []
                    def fake_docker(cmd, **kwargs):
                        calls.append(cmd)
                        if cmd[:3] == ["docker", "image", "inspect"]:
                            return subprocess.CompletedProcess(cmd, 0, b"", b"")
                        if cmd[:2] == ["docker", "run"]:
                            if failure == "timeout":
                                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
                            return subprocess.CompletedProcess(cmd, 4, b"core_tcp_bind_timeout", b"")
                        if cmd[:3] == ["docker", "rm", "--force"]:
                            return subprocess.CompletedProcess(cmd, 0, b"", b"")
                        raise AssertionError("unknown command")
                    with patch.object(core_stage, "STAGE", stage), \
                         patch.object(core_stage, "BINARY", binary), \
                         patch.object(core_stage, "inspect",
                                      return_value={"status": "verified_offline"}), \
                         patch.object(core_tcp_smoke.subprocess, "run",
                                      side_effect=fake_docker), \
                         self.assertRaises(core_tcp_smoke.ProbeError):
                        core_tcp_smoke.run_probe()
                    self.assertEqual(calls[-1][:3], ["docker", "rm", "--force"])
                    self.assertEqual(len(list(stage.glob(".loopback-probe.*"))), 0)

    def test_no_unverified_binary_or_unknown_image_is_run(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            binary = stage / "fake"
            binary.write_bytes(b"x")
            with patch.object(core_stage, "STAGE", stage), \
                 patch.object(core_stage, "BINARY", binary), \
                 patch.object(core_stage, "inspect",
                              return_value={"status": "verified_offline"}), \
                 patch.object(core_tcp_smoke.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 1, b"", b"")) as run:
                with self.assertRaisesRegex(core_tcp_smoke.ProbeError,
                                            "docker_image_not_present"):
                    core_tcp_smoke.run_probe()
                self.assertEqual(run.call_count, 1)
            with patch.object(core_stage, "inspect",
                              side_effect=core_stage.StageError("core_checksum_mismatch")), \
                 patch.object(core_tcp_smoke.subprocess, "run",
                              side_effect=AssertionError("docker should not be called")):
                with self.assertRaisesRegex(core_stage.StageError, "core_checksum_mismatch"):
                    core_tcp_smoke.run_probe()

    def test_release_script_and_version_remain_standalone(self):
        s = (ROOT / "scripts/core_tcp_smoke.py").read_text()
        self.assertNotIn("systemctl ", s)
        self.assertNotIn("docker compose up", s)
        self.assertNotIn("ssh ", s)
        self.assertIn("network_mode", s)
        self.assertIn("automatic_process_termination", s)


if __name__ == "__main__":
    unittest.main()
