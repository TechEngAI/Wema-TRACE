"""Deterministic assembly and integrity hashing for structured evidence packs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Sequence

from app.db.models import CaseModel, EventModel, SourceFileModel
from app.schemas.analysis import AnalysisResponse
from app.schemas.correlation import CaseCorrelationResult
from app.schemas.evidence import (
    EvidencePackCorrelation,
    EvidencePackEvidence,
    EvidencePackIntegrity,
    EvidencePackResponse,
    EvidencePackSummary,
    EvidencePackTimelineEvent,
    RawFileHashItem,
)
from app.schemas.enums import EventActionEnum, EventStatusEnum, InstitutionPartyEnum


PACK_SCHEMA_VERSION = "2026.1.0-evidence-pack"
_PACK_NAMESPACE = uuid.UUID("6a0990dc-5b6b-4f20-8575-7036108742cc")


def canonical_pack_bytes(pack: EvidencePackResponse) -> bytes:
    """Return exact UTF-8 canonical bytes hashed by Part 6.

    The complete JSON-ready pack is used except for ``integrity.sha256`` itself.
    Keys are lexicographically sorted and compact separators eliminate whitespace
    variation.  This is an integrity aid, not a signature or truth assertion.
    """
    payload = pack.model_dump(mode="json")
    payload["integrity"].pop("sha256", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _event_sort_key(event: EventModel) -> tuple[datetime, str]:
    return event.event_timestamp, str(event.id)


def build_evidence_pack(
    *,
    case: CaseModel,
    events: Sequence[EventModel],
    source_files: Sequence[SourceFileModel],
    analysis: AnalysisResponse,
    correlation: CaseCorrelationResult,
    generated_at: Optional[datetime] = None,
) -> EvidencePackResponse:
    """Build a validated read-only pack from authoritative existing outputs."""
    generated_at = generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must include timezone information")

    flag_map: Dict[uuid.UUID, list[str]] = {}
    for flag in analysis.data_quality_flags:
        for event_id in flag.affected_event_ids:
            flag_map.setdefault(event_id, []).append(flag.flag_code)

    timeline = []
    for event in sorted(events, key=_event_sort_key):
        raw_record = getattr(event, "raw_record", None)
        source_file_id = getattr(raw_record, "source_file_id", None) if raw_record else None
        timeline.append(
            EvidencePackTimelineEvent(
                event_id=event.id,
                event_timestamp=event.event_timestamp,
                institution=InstitutionPartyEnum(event.party),
                action=EventActionEnum(event.action),
                status=EventStatusEnum(event.status),
                amount_minor_units=event.amount_minor_units,
                currency=event.currency,
                session_id=event.session_id,
                rrn=event.rrn,
                reference=event.reference,
                raw_record_id=event.raw_record_id,
                source_file_id=source_file_id,
                data_quality_flags=sorted(flag_map.get(event.id, [])),
            )
        )

    pack_id = uuid.uuid5(_PACK_NAMESPACE, f"{case.id}:{generated_at.isoformat()}")
    pack = EvidencePackResponse(
        pack_schema_version=PACK_SCHEMA_VERSION,
        pack_id=pack_id,
        case_id=case.id,
        case_reference=case.reference,
        generated_at=generated_at,
        analysis_rule_version=analysis.rule_version,
        summary=EvidencePackSummary(
            outcome=analysis.outcome,
            strength=analysis.strength,
            outcome_reason=analysis.outcome_reason,
            strength_reason=analysis.strength_reason,
            scope_examined=analysis.scope_examined,
            disputed_amount_minor_units=case.disputed_amount,
            currency=case.currency,
            case_session_id=case.session_id,
            case_rrn=case.rrn,
        ),
        correlation=EvidencePackCorrelation(
            algorithm_version=correlation.algorithm_version,
            summary=correlation.summary,
            groups=correlation.groups,
        ),
        timeline=timeline,
        evidence=EvidencePackEvidence(
            supporting_event_ids=analysis.supporting_evidence,
            contradicting_event_ids=analysis.contradicting_evidence,
            missing_evidence=analysis.missing_evidence,
            alternative_explanations=analysis.alternative_explanations,
            recommended_next_evidence=analysis.recommended_next_evidence,
        ),
        data_quality_flags=analysis.data_quality_flags,
        limitations=[
            analysis.scope_examined or "Scope information was not supplied by the analysis.",
            "This pack reflects records available to TRACE at generation time; unobserved events may exist.",
            "Missing evidence is not proof that an event did not occur.",
        ],
        explanation=analysis.explanation,
        raw_file_hashes=[
            RawFileHashItem(
                institution=InstitutionPartyEnum(source.institution_source),
                filename=source.filename,
                sha256=source.sha256_hash,
            )
            for source in sorted(source_files, key=lambda item: (item.filename, str(item.id)))
        ],
        integrity=EvidencePackIntegrity(sha256=""),
    )
    digest = hashlib.sha256(canonical_pack_bytes(pack)).hexdigest()
    return pack.model_copy(update={"integrity": pack.integrity.model_copy(update={"sha256": digest})})
