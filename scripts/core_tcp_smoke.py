#!/usr/bin/env python3
"""v0.030: check a real Pushok Core TCP listener in a disposable no-network Docker container.

The host's proxy is NEVER started, stopped or switched. No ports published.
Container has a private loopback namespace; TCP success is NOT Telegram success.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
from app.pushok_core_config import render_staging_config
from scripts import core_stage

IMAGE = "ubuntu:24.04"
PORT = 18443
PROBE_TIMEOUT = 24
SHELL_PROBE = r"""
set -eu
/usr/local/bin/pushok-core /staging/probe.toml >/tmp/core.log 2>&1 &
pid=$!
cleanup() {
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}
trap cleanup EXIT
for i in $(seq 1 48); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo core_exited_before_bind
    exit 3
  fi
  if timeout 1 bash -c ': > /dev/tcp/127.0.0.1/18443' 2>/dev/null; then
    echo pushok_core_loopback_tcp_ready
    exit 0
  fi
  sleep 0.25
done
echo core_tcp_bind_timeout
exit 4
"""


class ProbeError(Exception):
    pass


def safe_config() -> str:
    """Generate a fresh throwaway secret, never return it through the API or logs."""
    config = render_staging_config(
        tls_domain="proxy.example.org", port=PORT, offline_smoke=True,
    )
    parsed = tomllib.loads(config)
    if (parsed["server"]["port"] != PORT
            or parsed["server"]["api"]["enabled"] is not False
            or parsed["server"]["listeners"] != [{"ip": "127.0.0.1"}]
            or parsed["general"]["use_middle_proxy"] is not False
            or parsed["censorship"]["mask"] is not False
            or parsed["censorship"]["tls_emulation"] is not False
            or parsed["general"]["modes"] != {
                "classic": False, "secure": False, "tls": True
            }
            or "ad_tag" in parsed["general"]):
        raise ProbeError("unsafe_isolated_staging_configuration")
    return config


def docker_command(*, probe_dir: Path, name: str) -> list[str]:
    if not name.startswith("pushok-core-smoke-"):
        raise ProbeError("invalid_container_name")
    return [
        "docker", "run", "--rm", "--pull", "never",
        "--name", name, "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
        "--tmpfs", "/tmp:rw,nosuid,noexec,size=8m",
        "--tmpfs", "/run:rw,nosuid,noexec,size=8m",
        "--mount", f"type=bind,source={core_stage.BINARY},target=/usr/local/bin/pushok-core,readonly",
        "--mount", f"type=bind,source={probe_dir},target=/staging,readonly",
        IMAGE, "bash", "-c", SHELL_PROBE,
    ]


def run_probe() -> dict:
    """Requires a previously built and verified local binary; no source downloads."""
    if os.geteuid() == 0:
        raise ProbeError("root_is_not_allowed")
    report = core_stage.inspect()
    if report["status"] != "verified_offline":
        raise ProbeError("core_not_verified")
    if not core_stage.STAGE.is_dir() or core_stage.STAGE.is_symlink():
        raise ProbeError("invalid_staging_directory")
    if not core_stage.BINARY.is_file() or core_stage.BINARY.is_symlink():
        raise ProbeError("invalid_core_binary")
    config = safe_config()
    # Verify Docker image availability without implicitly downloading it.
    check = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, timeout=8,
    )
    if check.returncode != 0:
        raise ProbeError("docker_image_not_present_pull_explicitly")
    name = "pushok-core-smoke-" + uuid4().hex[:16]
    # Temporary directory and config are removed even when Docker fails.
    with tempfile.TemporaryDirectory(prefix=".loopback-probe.", dir=core_stage.STAGE) as tmp:
        target = Path(tmp) / "probe.toml"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(config)
        cmd = docker_command(probe_dir=Path(tmp), name=name)
        try:
            child = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT)
            if (child.returncode != 0
                    or child.stdout.strip() != b"pushok_core_loopback_tcp_ready"):
                # Never echo stderr/stdout; upstream logs can contain sensitive data.
                raise ProbeError("isolated_tcp_probe_failed")
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("isolated_tcp_probe_timed_out") from exc
        finally:
            # No success claim until the short-lived container is guaranteed stopped.
            cleanup = subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True, timeout=8,
            )
            # "No such container" is expected after --rm; any other refusal is unsafe.
            if cleanup.returncode != 0 and b"No such container" not in cleanup.stderr:
                raise ProbeError("staging_container_cleanup_not_confirmed")
    return {
        "status": "isolated_tcp_accept_verified",
        "network_mode": "none",
        "public_ports": 0,
        "container_removed": True,
        "host_runtime_changed": False,
        "telegram_verified": False,
        "mtproxy_handshake_verified": False,
        "sponsor_verified": False,
        "rollback_type": "automatic_process_termination",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pushok Core: изолированная проверка TCP")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true", help="Без запуска и изменения файлов")
    group.add_argument("--run", action="store_true", help="Тест только в Docker --network none")
    args = parser.parse_args()
    if args.plan:
        print("ПЛАН: только Docker --network none, локальный 127.0.0.1:18443, без опубликованных портов.")
        print("Проверяем реальный TCP accept; Telegram, MTProxy handshake и спонсор не проверяются.")
        print("Процесс завершится с автоматическим удалением контейнера. VPS и systemd не затрагиваются.")
        return 0
    try:
        print(json.dumps(run_probe(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ProbeError, core_stage.StageError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, (ProbeError, core_stage.StageError)) else "local_runtime_error"
        print("Отказ: " + reason, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
