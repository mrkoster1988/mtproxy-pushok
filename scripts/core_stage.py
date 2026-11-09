#!/usr/bin/env python3
"""Pushok Source Core v0.029: offline binary attestation and reversible staging marker.

This script never starts a listening socket, contacts Telegram, invokes systemd,
or swaps a real running MTProxy. "Activation" only records a local TEST CANDIDATE.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SHA = "66708d2866f3f3a4541c4fa67984d408e71a8af4"
VERSION = "0.027"  # The Rust binary's version; the control-plane release is v0.029.
NAME = f"pushok-core-{VERSION}-{CORE_SHA}"
STAGE = ROOT / ".build" / "pushok-core"
OUTPUT = STAGE / "output"
BINARY = OUTPUT / NAME
CHECKSUM = OUTPUT / (NAME + ".sha256")
STATE = STAGE / "staging-state.json"
LOCK = STAGE / ".staging-state.lock"
EXPECTED = {
    "product": "Pushok Source Core",
    "version": VERSION,
    "upstream_commit": CORE_SHA,
    "patch": "cli-build-info-001",
    "proxy_started": False,
}


class StageError(Exception):
    pass


def assert_local_path(path: Path) -> None:
    """Reject symlinks in any mutable build-path component; avoid arbitrary paths."""
    for candidate in (ROOT / ".build", STAGE, OUTPUT, path):
        if candidate.is_symlink():
            raise StageError("staging_symlink_forbidden")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect() -> dict:
    """Verify the fixed, pinned local artifact BEFORE executing the bounded CLI probe."""
    for path in (BINARY, CHECKSUM, OUTPUT / "TELEMT-LICENSE",
                 OUTPUT / "NOTICE.md", OUTPUT / "PATCHES.md"):
        assert_local_path(path)
        if not path.is_file():
            raise StageError("built_core_artifact_missing")
    if not BINARY.stat().st_mode & 0o111:
        raise StageError("binary_not_executable")
    checksum_text = CHECKSUM.read_text(encoding="ascii", errors="strict").strip()
    match = re.fullmatch(r"([0-9a-f]{64}) [ *]" + re.escape(NAME), checksum_text)
    if match is None:
        raise StageError("invalid_checksum_manifest")
    digest = _hash_file(BINARY)
    if digest != match.group(1):
        raise StageError("core_checksum_mismatch")
    license_source = ROOT / "third_party" / "telemt" / "LICENSE"
    if (OUTPUT / "TELEMT-LICENSE").read_bytes() != license_source.read_bytes():
        raise StageError("core_license_mismatch")
    if not (OUTPUT / "NOTICE.md").stat().st_size or not (OUTPUT / "PATCHES.md").stat().st_size:
        raise StageError("missing_modifications_notice")

    # Execute only a build whose digest is already confirmed. The CLI branch
    # returns before any proxy/TLS/runtime initialization in the pinned Rust patch.
    process = subprocess.Popen(
        [str(BINARY), "--pushok-build-info"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(OUTPUT),
        env={"PATH": os.defpath, "HOME": "/nonexistent"},
    )
    try:
        stdout, stderr = process.communicate(timeout=6)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise StageError("core_cli_timeout") from exc
    if process.returncode != 0 or len(stdout) > 2048 or stderr:
        raise StageError("core_cli_probe_failed")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise StageError("invalid_core_build_info") from exc
    if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in EXPECTED.items()):
        raise StageError("unexpected_core_identity")
    return {
        "status": "verified_offline",
        "binary_version": VERSION,
        "sha256": digest,
        "source_commit": CORE_SHA,
        "cli_verified": True,
        "checksum_verified": True,
        "license_verified": True,
        "tcp_tested": False,
        "telegram_tested": False,
        "sponsor_tested": False,
        "runtime_started": False,
    }


def load_state() -> dict:
    assert_local_path(STATE)
    if not STATE.exists():
        return {"schema_version": 1, "active": None, "previous": None}
    if STATE.stat().st_size > 2048:
        raise StageError("invalid_staging_state")
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise StageError("invalid_staging_state") from exc
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise StageError("invalid_staging_state")
    if set(state) != {"schema_version", "active", "previous"}:
        raise StageError("invalid_staging_state")
    for name in ("active", "previous"):
        value = state[name]
        if value is not None and (
            not isinstance(value, dict) or
            set(value) != {"sha256", "binary_version"} or
            not isinstance(value["sha256"], str) or
            re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None or
            value["binary_version"] != VERSION
        ):
            raise StageError("invalid_staging_state")
    return state


@contextmanager
def locked_state():
    assert_local_path(STAGE)
    assert_local_path(LOCK)
    if not STAGE.is_dir():
        raise StageError("staging_directory_missing")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(LOCK, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save_state(state: dict) -> None:
    assert_local_path(STATE)
    fd, path = tempfile.mkstemp(prefix=".staging-state.", dir=STAGE)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(path, STATE)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pushok Core: безопасная локальная диагностика и маркер тестового кандидата")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--plan", action="store_true", help="Ничего не менять и не запускать")
    actions.add_argument("--inspect", action="store_true", help="Проверить локальный бинарник без запуска proxy")
    actions.add_argument("--activate", action="store_true", help="Только пометить тестовый бинарник кандидатом")
    actions.add_argument("--rollback", action="store_true", help="Только вернуть предыдущий маркер тестового кандидата")
    args = parser.parse_args()

    if args.plan:
        print("ПЛАН: проверка sha256, лицензии и --pushok-build-info; без запуска MTProxy.")
        print("Активация/откат — только атомарный маркер в .build/, не systemd или Docker.")
        return 0
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("Отказ: запускать диагностику и тестовый маркер нужно без sudo.", file=sys.stderr)
        return 2

    try:
        if args.inspect:
            print(json.dumps(inspect(), ensure_ascii=False, sort_keys=True))
            return 0
        with locked_state():
            current = load_state()
            if args.activate:
                result = inspect()
                candidate = {"sha256": result["sha256"], "binary_version": VERSION}
                if current["active"] != candidate:
                    current = {"schema_version": 1, "active": candidate,
                               "previous": current["active"]}
                    save_state(current)
                message = "staging_candidate_marked"
            else:
                if current["active"] is None:
                    raise StageError("no_staging_candidate_to_rollback")
                # Never point a marker to a disappeared previous binary.
                # Only this single pinned artifact is supported in v0.029.
                if current["previous"] is not None:
                    verified = inspect()
                    if verified["sha256"] != current["previous"]["sha256"]:
                        raise StageError("previous_staging_binary_not_present")
                # Revert only the manifest. Never touch running processes or switch proxy.
                current = {"schema_version": 1, "active": current["previous"],
                           "previous": None}
                save_state(current)
                message = "staging_marker_rolled_back"
        print(json.dumps({"status": message, "marker_only": True,
                          "runtime_changed": False, "active": current["active"] is not None},
                         ensure_ascii=False, sort_keys=True))
        return 0
    except (StageError, OSError) as exc:
        reason = str(exc) if isinstance(exc, StageError) else "local_filesystem_error"
        print("Отказ: " + reason, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
