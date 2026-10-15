import json
import unittest

from evidence_exporter import EvidenceExporter


class EvidenceExporterTests(unittest.TestCase):
    def setUp(self):
        self.rules_result = {
            "case_reference": "CASE-77",
            "session_reference": "session/exact-0042",
            "amount": "$18.25 USD",
            "investigation_scope": "Payment status review",
            "timestamps": {"created_at": "2026-10-15T12:00:00Z"},
            "rule_version": "rules-v3",
            "outcome": "pending",
            "strength": "moderate",
            "supporting_evidence": [{"event_id": "evt-1"}],
            "contradicting_evidence": [],
            "data_quality_flags": ["merchant_name_missing"],
        }
        self.timeline = [{"event_id": "evt-1", "at": "2026-10-15T12:00:00Z"}]
        self.missing = ["settlement record", "merchant response"]
        self.ai_metadata = {"explanation": "The status remains pending.", "model": "test"}

    def test_pack_contains_required_fields_and_mock_hash_limitation(self):
        pack = EvidenceExporter().build_evidence_pack(
            self.rules_result,
            self.timeline,
            self.missing,
            self.ai_metadata,
            exported_at="2026-10-15T13:00:00Z",
        )

        self.assertEqual(pack["case_reference"], "CASE-77")
        self.assertEqual(pack["session_reference"], "session/exact-0042")
        self.assertEqual(pack["amount"], "$18.25 USD")
        self.assertEqual(pack["outcome"], "pending")
        self.assertEqual(pack["supporting_evidence"], [{"event_id": "evt-1"}])
        self.assertEqual(pack["contradicting_evidence"], [])
        self.assertEqual(pack["data_quality_flags"], ["merchant_name_missing"])
        self.assertTrue(pack["mock_payload_hash"].startswith("MOCK-SHA256:"))
        self.assertIn("does not prove", pack["hash_limitation_note"])

    def test_email_uses_exact_reference_amount_and_missing_evidence(self):
        email = EvidenceExporter().generate_complaint_email_draft(
            self.missing,
            self.rules_result["session_reference"],
            self.rules_result["amount"],
        )

        self.assertIn("Session reference: session/exact-0042", email)
        self.assertIn("Amount: $18.25 USD", email)
        self.assertIn("settlement record", email)
        self.assertIn("merchant response", email)
        self.assertNotIn("merchant name", email.lower())
        self.assertNotIn("refund was issued", email.lower())

    def test_export_json_is_valid_and_requires_exact_transaction_details(self):
        exporter = EvidenceExporter()
        encoded = exporter.export_json(
            self.rules_result,
            self.timeline,
            self.missing,
            self.ai_metadata,
            exported_at="2026-10-15T13:00:00Z",
        )
        self.assertEqual(json.loads(encoded)["rule_version"], "rules-v3")

        with self.assertRaises(ValueError):
            exporter.generate_complaint_email_draft(self.missing, "", None)


if __name__ == "__main__":
    unittest.main()