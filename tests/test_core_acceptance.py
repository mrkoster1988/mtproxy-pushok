"""v0.032: production gates fail closed and never infer real readiness from CI."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from app.core_acceptance import GATES, evaluate_release_gates


class CoreAcceptanceTests(unittest.TestCase):
    def test_empty_evidence_is_not_ready(self):
        result = evaluate_release_gates({})
        self.assertFalse(result["ready_for_production"])
        self.assertEqual(len(result["pending"]), len(GATES))

    def test_ci_evidence_is_not_telegram_evidence(self):
        result = evaluate_release_gates({"artifact": True, "isolated_tcp": True})
        self.assertFalse(result["ready_for_production"])
        self.assertIn("telegram_client", result["pending"])
        self.assertIn("sponsor_delivery", result["pending"])

    def test_non_boolean_values_fail_closed(self):
        for value in (1, "true", None, [], {}):
            result = evaluate_release_gates({"artifact": value})
            self.assertIn("artifact", result["pending"])

    def test_unknown_gate_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_release_gates({"production_is_fine": True})

    def test_complete_explicit_evidence(self):
        result = evaluate_release_gates({name: True for name, _ in GATES})
        self.assertTrue(result["ready_for_production"])
        self.assertEqual(result["pending"], [])


if __name__ == "__main__":
    unittest.main()
