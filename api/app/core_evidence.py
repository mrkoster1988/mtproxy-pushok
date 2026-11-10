"""Offline signature validation for Pushok Core acceptance receipts.

The trusted external issuer must perform actual staging checks; this module
only validates supplied receipts and never accesses a node or changes services.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .core_acceptance import GATES, evaluate_release_gates

SCHEMA_VERSION = 1
MAX_VALIDITY_SECONDS = 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 60
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
ISSUER_PATTERN = re.compile(r"[a-zA-Z0-9_.:-]{3,100}\Z")


def canonical_payload(receipt: Mapping[str, Any]) -> bytes:
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def verify_receipt(envelope: Mapping[str, Any], *, key: bytes, expected_version: str,
                   expected_sha256: str, trusted_issuers: frozenset[str],
                   now: datetime | None = None) -> dict:
    """Fail closed on a missing/altered/stale receipt or mismatched binary."""
    failed = {"verified": False, "reason": "invalid_receipt", "readiness": None}
    if not isinstance(envelope, Mapping) or set(envelope) != {"receipt", "signature"}:
        return failed
    receipt, signature = envelope["receipt"], envelope["signature"]
    if not isinstance(receipt, dict) or not isinstance(signature, str):
        return failed
    required = {"schema", "issuer", "project_version", "artifact_sha256", "issued_at",
                "expires_at", "checks"}
    if set(receipt) != required or receipt.get("schema") != SCHEMA_VERSION:
        return failed
    if not isinstance(expected_version, str) or not isinstance(expected_sha256, str):
        return failed
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        return failed
    if receipt["project_version"] != expected_version or receipt["artifact_sha256"] != expected_sha256:
        return failed
    issuer = receipt["issuer"]
    if not isinstance(issuer, str) or not ISSUER_PATTERN.fullmatch(issuer) or issuer not in trusted_issuers:
        return failed
    if type(receipt["issued_at"]) is not int or type(receipt["expires_at"]) is not int:
        return failed
    issued_at, expires_at = receipt["issued_at"], receipt["expires_at"]
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        return failed
    timestamp = int(instant.timestamp())
    if issued_at > timestamp + MAX_CLOCK_SKEW_SECONDS or expires_at <= timestamp:
        return failed
    if expires_at <= issued_at or expires_at - issued_at > MAX_VALIDITY_SECONDS:
        return failed
    checks = receipt["checks"]
    names = {name for name, _ in GATES}
    if not isinstance(checks, dict) or set(checks) != names:
        return failed
    if any(type(value) is not bool for value in checks.values()):
        return failed
    if not isinstance(key, bytes) or len(key) < 32:
        return failed
    if not SHA256_PATTERN.fullmatch(signature):
        return failed
    try:
        digest = hmac.new(key, canonical_payload(receipt), hashlib.sha256).hexdigest()
    except (TypeError, ValueError, OverflowError):
        return failed
    if not hmac.compare_digest(digest, signature):
        return failed
    return {"verified": True, "reason": "valid_signature_and_context",
            "readiness": evaluate_release_gates(checks),
            "issuer": issuer, "expires_at": expires_at}
