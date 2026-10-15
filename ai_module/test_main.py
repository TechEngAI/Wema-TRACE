import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app


class FakeExplanationService:
    def __init__(self, result="A supported explanation.", error=None):
        self.result = result
        self.error = error

    def explain(self, analysis):
        if self.error:
            raise self.error
        return self.result


class MainEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.payload = {
            "case_id": "CASE-7",
            "outcome": "pending",
            "strength": "moderate",
            "timeline": [{"event_id": "evt-1", "detail": "Rule matched"}],
            "supporting_event_ids": ["evt-1"],
            "session_reference": "session-exact-0042",
            "amount": "$5.00 USD",
            "missing_evidence": ["receipt"],
        }

    def test_explain_returns_validated_explanation(self):
        with patch("main.explanation_service", FakeExplanationService()):
            response = self.client.post("/api/v1/explain", json=self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["case_id"], "CASE-7")
        self.assertEqual(response.json()["explanation"], "A supported explanation.")

    def test_explain_service_error_returns_clear_json_error(self):
        with patch(
            "main.explanation_service",
            FakeExplanationService(error=RuntimeError("test failure")),
        ):
            response = self.client.post("/api/v1/explain", json=self.payload)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "explanation_error")
        self.assertEqual(
            response.json()["error"]["message"], "Could not generate explanation."
        )

    def test_export_returns_pack_and_complaint_draft(self):
        with patch("main.explanation_service", FakeExplanationService()):
            response = self.client.post("/api/v1/export-evidence", json=self.payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["case_reference"], "CASE-7")
        self.assertEqual(body["session_reference"], "session-exact-0042")
        self.assertEqual(body["amount"], "$5.00 USD")
        self.assertIn("complaint_email_draft", body)
        self.assertIn("mock_payload_hash", body)

    def test_export_without_exact_transaction_details_returns_json_error(self):
        payload = dict(self.payload, session_reference=None, amount=None)
        response = self.client.post("/api/v1/export-evidence", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"], "missing_transaction_details"
        )

    def test_missing_required_analysis_field_returns_validation_error(self):
        payload = dict(self.payload)
        del payload["supporting_event_ids"]
        response = self.client.post("/api/v1/explain", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")


if __name__ == "__main__":
    unittest.main()