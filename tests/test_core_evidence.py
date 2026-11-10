"""v0.032 isolated tests for signed acceptance receipts."""
import hashlib
import hmac
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.core_acceptance import GATES
from app.core_evidence import canonical_payload, verify_receipt

KEY = b"test-only-secret" * 4
VERSION = "0.032"
DIGEST = "a" * 64
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def signed(checks=None, **overrides):
    receipt = {
        "schema": 1, "issuer": "staging-operator-01", "project_version": VERSION,
        "artifact_sha256": DIGEST, "issued_at": int(NOW.timestamp()) - 10,
        "expires_at": int(NOW.timestamp()) + 120,
        "checks": checks if checks is not None else {name: False for name, _ in GATES},
    }
    receipt.update(overrides)
    return {"receipt": receipt,
            "signature": hmac.new(KEY, canonical_payload(receipt), hashlib.sha256).hexdigest()}


def check(envelope):
    return verify_receipt(envelope, key=KEY, expected_version=VERSION,
                          expected_sha256=DIGEST,
                          trusted_issuers=frozenset({"staging-operator-01"}), now=NOW)


class EvidenceTests(unittest.TestCase):
    def test_valid_receipt_but_unready(self):
        result = check(signed())
        self.assertTrue(result["verified"])
        self.assertFalse(result["readiness"]["ready_for_production"])

    def test_explicitly_all_passed(self):
        receipt = signed({name: True for name, _ in GATES})
        self.assertTrue(check(receipt)["readiness"]["ready_for_production"])

    def test_tamper_detected(self):
        receipt = signed()
        receipt["receipt"]["checks"]["artifact"] = True
        self.assertFalse(check(receipt)["verified"])

    def test_wrong_artifact_and_version_rejected(self):
        self.assertFalse(check(signed(artifact_sha256="b" * 64))["verified"])
        self.assertFalse(check(signed(project_version="0.031"))["verified"])

    def test_unknown_issuer_and_bad_expiry_rejected(self):
        self.assertFalse(check(signed(issuer="untrusted-issuer"))["verified"])
        self.assertFalse(check(signed(expires_at=int(NOW.timestamp()) - 1))["verified"])
        self.assertFalse(check(signed(expires_at=int(NOW.timestamp()) + 90000))["verified"])

    def test_future_and_invalid_types_rejected(self):
        self.assertFalse(check(signed(issued_at=int(NOW.timestamp()) + 61))["verified"])
        self.assertFalse(check(signed(issued_at=True))["verified"])
        self.assertFalse(check(signed(checks={name: 1 for name, _ in GATES}))["verified"])

    def test_unknown_check_rejected(self):
        self.assertFalse(check(signed(checks={"artifact": True}))["verified"])

    def test_no_empty_or_short_key(self):
        env = signed()
        self.assertFalse(verify_receipt(env, key=b"short", expected_version=VERSION,
                                        expected_sha256=DIGEST,
                                        trusted_issuers=frozenset({"staging-operator-01"}),
                                        now=NOW)["verified"])


if __name__ == "__main__":
    unittest.main()
