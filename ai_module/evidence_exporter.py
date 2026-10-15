import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence


class EvidenceExporter:
    HASH_LIMITATION = (
        "This mock payload hash can help detect later changes to this exported payload; "
        "it does not prove that the underlying records are true or complete."
    )

    @staticmethod
    def _canonical_json(payload: Mapping[str, Any]) -> str:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )

    @staticmethod
    def _value(
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        key: str,
        default: Any = None,
    ) -> Any:
        if key in primary:
            return primary[key]
        return fallback.get(key, default)

    def generate_complaint_email_draft(
        self,
        missing_evidence: Sequence[Any],
        session_reference: str,
        amount: Any,
    ) -> str:
        """Build a factual email using only exact supplied identifiers and amount."""
        if not session_reference or amount is None or str(amount) == "":
            raise ValueError("Exact session_reference and amount are required for the email draft")

        missing_items = [str(item) for item in missing_evidence]
        if missing_items:
            evidence_text = "\n".join(f"- {item}" for item in missing_items)
        else:
            evidence_text = "- No missing evidence was supplied."

        return (
            "Subject: Request for review of transaction\n\n"
            "Hello,\n\n"
            "Please review the transaction identified below.\n"
            f"Session reference: {session_reference}\n"
            f"Amount: {amount}\n\n"
            "Evidence still needed:\n"
            f"{evidence_text}\n\n"
            "Please provide the records needed to clarify this transaction.\n\n"
            "Regards,\n"
            "[Your name]"
        )

    def build_evidence_pack(
        self,
        rules_engine_result: Mapping[str, Any],
        timeline: Sequence[Any],
        missing_evidence: Sequence[Any],
        ai_metadata: Mapping[str, Any],
        *,
        session_reference: Optional[str] = None,
        amount: Any = None,
        exported_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a JSON-compatible evidence export from saved, deterministic inputs."""
        resolved_session = session_reference
        if resolved_session is None:
            resolved_session = rules_engine_result.get(
                "session_reference",
                rules_engine_result.get(
                    "session_ref", ai_metadata.get("session_reference")
                ),
            )
        resolved_amount = amount if amount is not None else self._value(
            rules_engine_result, ai_metadata, "amount"
        )
        case_reference = rules_engine_result.get(
            "case_reference",
            rules_engine_result.get("case_ref", resolved_session),
        )
        timestamps = rules_engine_result.get("timestamps")
        if timestamps is None:
            timestamps = {
                key: rules_engine_result[key]
                for key in ("created_at", "updated_at", "investigated_at")
                if key in rules_engine_result
            }

        pack: Dict[str, Any] = {
            "case_reference": case_reference,
            "session_reference": resolved_session,
            "amount": resolved_amount,
            "investigation_scope": rules_engine_result.get("investigation_scope"),
            "timestamps": {
                **(timestamps if isinstance(timestamps, Mapping) else {"source": timestamps}),
                "exported_at": exported_at
                or datetime.now(timezone.utc).isoformat(),
            },
            "rule_version": rules_engine_result.get("rule_version"),
            "outcome": rules_engine_result.get("outcome"),
            "strength": rules_engine_result.get("strength"),
            "explanation": ai_metadata.get("explanation"),
            "supporting_evidence": rules_engine_result.get(
                "supporting_evidence",
                rules_engine_result.get(
                    "supporting_events",
                    rules_engine_result.get("supporting_event_ids", []),
                ),
            ),
            "contradicting_evidence": rules_engine_result.get(
                "contradicting_evidence",
                rules_engine_result.get(
                    "contradicting_events",
                    rules_engine_result.get("contradicting_event_ids", []),
                ),
            ),
            "timeline": list(timeline),
            "missing_evidence": list(missing_evidence),
            "data_quality_flags": rules_engine_result.get("data_quality_flags", []),
            "ai_metadata": dict(ai_metadata),
            "complaint_email_draft": self.generate_complaint_email_draft(
                missing_evidence,
                str(resolved_session) if resolved_session is not None else "",
                resolved_amount,
            ),
            "hash_limitation_note": self.HASH_LIMITATION,
        }
        payload_hash = hashlib.sha256(self._canonical_json(pack).encode("utf-8")).hexdigest()
        pack["mock_payload_hash"] = f"MOCK-SHA256:{payload_hash}"
        return pack

    def export_json(self, *args: Any, **kwargs: Any) -> str:
        return json.dumps(self.build_evidence_pack(*args, **kwargs), indent=2, ensure_ascii=True, default=str)