import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from explanation_service import AIExplanationService


class AIExplanationServiceTests(unittest.TestCase):
    def setUp(self):
        self.analysis = {
            "outcome": "declined",
            "supporting_event_ids": ["evt-1"],
            "timeline": [{"event_id": "evt-1", "detail": "Rule matched"}],
        }

    def test_prompt_preserves_outcome_and_limits_evidence(self):
        prompt = AIExplanationService(lambda _: "").build_prompt(self.analysis)

        self.assertIn("do not change, contradict, or reinterpret it", prompt)
        self.assertIn("Do not invent or cite any event ID", prompt)
        self.assertIn('"supporting_event_ids": ["evt-1"]', prompt)
        self.assertNotIn("evt-2", prompt)

    def test_invalid_event_id_retries_once_then_returns_valid_response(self):
        responses = iter(("Finding supported by [evt-2].", "Finding supported by [evt-1]."))
        prompts = []

        def llm(prompt):
            prompts.append(prompt)
            return next(responses)

        result = AIExplanationService(llm).explain(self.analysis)

        self.assertEqual(result, "Finding supported by [evt-1].")
        self.assertEqual(len(prompts), 2)
        self.assertIn("previous answer did not satisfy", prompts[1])

    def test_repeated_invalid_ids_return_static_fallback(self):
        service = AIExplanationService(lambda _: "Finding supported by [evt-2].")

        self.assertEqual(service.explain(self.analysis), service.FALLBACK_RESPONSE)

    def test_response_must_be_under_80_words(self):
        response = "word " * 80
        service = AIExplanationService(lambda _: response)

        self.assertEqual(service.explain(self.analysis), service.FALLBACK_RESPONSE)

    def test_financial_claim_needs_matching_completed_timeline_entry(self):
        analysis = dict(self.analysis)
        analysis["timeline"] = [
            {"event_id": "evt-1", "detail": "Refund requested"},
            {"event_id": "evt-2", "detail": "Case completed"},
        ]
        service = AIExplanationService(lambda _: "A refund was issued.")

        self.assertEqual(service.explain(analysis), service.FALLBACK_RESPONSE)

    def test_negated_financial_event_does_not_support_claim(self):
        analysis = dict(self.analysis)
        analysis["timeline"] = [
            {"event_id": "evt-1", "detail": "Refund not issued"},
        ]
        service = AIExplanationService(lambda _: "A refund was issued.")

        self.assertEqual(service.explain(analysis), service.FALLBACK_RESPONSE)

    def test_financial_claim_is_allowed_when_timeline_explicitly_records_it(self):
        analysis = dict(self.analysis)
        analysis["timeline"] = [
            {"event_id": "evt-1", "detail": "Refund issued"},
        ]
        service = AIExplanationService(lambda _: "A refund was issued.")

        self.assertEqual(service.explain(analysis), "A refund was issued.")

    def test_missing_api_key_returns_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            service = AIExplanationService()

        self.assertEqual(service.explain(self.analysis), service.FALLBACK_RESPONSE)

    def test_api_key_is_loaded_from_environment(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            service = AIExplanationService()

        self.assertEqual(service._api_key, "test-key")

    def test_groq_compatible_client_receives_analysis_in_system_message(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Finding supported by [evt-1]."))]
        )
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return response

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        service = AIExplanationService(api_key="test-key", client=client)

        self.assertEqual(service.explain(self.analysis), "Finding supported by [evt-1].")
        self.assertIn('"outcome": "declined"', calls[0]["messages"][0]["content"])
        self.assertEqual(calls[0]["messages"][0]["role"], "system")

    def test_provider_failure_returns_fallback_without_raising(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("provider unavailable")

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        service = AIExplanationService(api_key="test-key", client=client)

        self.assertEqual(service.explain(self.analysis), service.FALLBACK_RESPONSE)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
