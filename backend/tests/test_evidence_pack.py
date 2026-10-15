"""Part 6 structured evidence-pack and integrity tests (synthetic data only)."""

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.correlation.engine import correlate_events
from app.db.models import CaseModel, EventModel, SourceFileModel
from app.evidence_pack.service import build_evidence_pack, canonical_pack_bytes
from app.explanations.service import ExplanationService
from app.schemas.analysis import AnalysisResponse, DataQualityFlagItem, TimelineEvent
from app.schemas.enums import (
    EventActionEnum, EventStatusEnum, FlagSeverityEnum, InstitutionPartyEnum,
    OutcomeEnum, StrengthEnum,
)

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def make_case():
    return CaseModel(
        id=uuid4(), reference="TRC-PACK-001", initiating_party="ORIGINATING_BANK",
        counterparty="RECEIVING_BANK", disputed_amount=8500000, currency="NGN",
        status="OPEN", session_id="PACK-S", rrn="PACK-R", metadata_json={}, created_at=T0,
    )


def make_event(case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS", timestamp=T0):
    return EventModel(
        id=uuid4(), case_id=case_id, raw_record_id=uuid4(), party=party, action=action,
        status=status, amount_minor_units=8500000, currency="NGN", session_id="PACK-S",
        rrn="PACK-R", reference="PACK-REF", channel="MOBILE", event_timestamp=timestamp,
        metadata_json={},
    )


def make_analysis(case, events, outcome=OutcomeEnum.INSUFFICIENT_EVIDENCE, strength=StrengthEnum.INSUFFICIENT):
    event = events[0] if events else None
    timeline = [] if event is None else [TimelineEvent(
        event_id=event.id, raw_record_id=event.raw_record_id, timestamp=event.event_timestamp,
        party=InstitutionPartyEnum(event.party), action=EventActionEnum(event.action),
        status=EventStatusEnum(event.status), amount_minor_units=event.amount_minor_units,
        currency=event.currency,
    )]
    analysis = AnalysisResponse(
        case_id=case.id, outcome=outcome, strength=strength, rule_version="2026.1.0-rules",
        category="TEST", timeline=timeline, supporting_event_ids=[event.id] if event else [],
        supporting_evidence=[event.id] if event else [], outcome_reason="Fixed deterministic rationale.",
        strength_reason="Fixed deterministic strength rationale.", scope_examined="Synthetic case scope.",
        alternative_explanations=["Additional records may exist."],
    )
    return analysis.model_copy(update={"explanation": ExplanationService(enabled=False).explain(analysis)})


def make_pack(case=None, events=None, generated_at=T0):
    case = case or make_case()
    events = events if events is not None else [make_event(case.id)]
    analysis = make_analysis(case, events)
    correlation = correlate_events(events, case.id)
    source = SourceFileModel(
        id=uuid4(), case_id=case.id, filename="synthetic.csv", file_type="CSV",
        institution_source="ORIGINATING_BANK", sha256_hash="a" * 64, file_size_bytes=1,
        record_count=len(events), uploaded_at=T0,
    )
    return build_evidence_pack(
        case=case, events=events, source_files=[source], analysis=analysis,
        correlation=correlation, generated_at=generated_at,
    )


def test_complete_pack_preserves_authoritative_sections_and_explanation():
    pack = make_pack()
    assert pack.summary.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert pack.summary.strength == StrengthEnum.INSUFFICIENT
    assert pack.explanation.summary
    assert pack.evidence.supporting_event_ids
    assert not set(pack.evidence.supporting_event_ids) & set(pack.evidence.contradicting_event_ids)
    assert pack.raw_file_hashes[0].sha256 == "a" * 64


@pytest.mark.parametrize("outcome", list(OutcomeEnum))
@pytest.mark.parametrize("strength", list(StrengthEnum))
def test_pack_preserves_all_outcomes_and_strengths(outcome, strength):
    case = make_case()
    events = [make_event(case.id)]
    analysis = make_analysis(case, events, outcome, strength)
    pack = build_evidence_pack(
        case=case, events=events, source_files=[], analysis=analysis,
        correlation=correlate_events(events, case.id), generated_at=T0,
    )
    assert pack.summary.outcome == outcome
    assert pack.summary.strength == strength


def test_hash_uses_documented_canonical_representation_and_excludes_self():
    pack = make_pack()
    assert hashlib.sha256(canonical_pack_bytes(pack)).hexdigest() == pack.integrity.sha256
    changed = pack.model_copy(update={"case_reference": "CHANGED"})
    assert hashlib.sha256(canonical_pack_bytes(changed)).hexdigest() != pack.integrity.sha256
    assert "not a digital signature" in pack.integrity.limitation


def test_identical_fixed_payload_has_identical_hash_and_equal_timestamp_order_is_stable():
    case = make_case()
    first = make_event(case.id, timestamp=T0)
    second = make_event(case.id, party="SWITCH", action="SWITCH_OUT", timestamp=T0)
    pack_a = make_pack(case, [second, first])
    pack_b = make_pack(case, [second, first])
    assert pack_a.integrity.sha256 == pack_b.integrity.sha256
    assert [str(item.event_id) for item in pack_a.timeline] == sorted([str(first.id), str(second.id)])


def test_missing_optional_and_empty_evidence_lists_are_preserved_without_fabrication():
    case = make_case()
    analysis = make_analysis(case, [])
    analysis = analysis.model_copy(update={
        "supporting_evidence": [], "contradicting_evidence": [], "missing_evidence": [],
        "recommended_next_evidence": [], "alternative_explanations": [],
    })
    pack = build_evidence_pack(
        case=case, events=[], source_files=[], analysis=analysis,
        correlation=correlate_events([], case.id), generated_at=T0,
    )
    assert pack.timeline == [] and pack.evidence.supporting_event_ids == []
    assert any("not proof" in item for item in pack.limitations)


def test_flags_and_evidence_categories_remain_separate():
    case = make_case()
    event = make_event(case.id)
    analysis = make_analysis(case, [event])
    flag = DataQualityFlagItem(
        flag_code="SYNTHETIC_FLAG", severity=FlagSeverityEnum.LOW, description="Synthetic",
        affected_event_ids=[event.id], impact="Caveat",
    )
    analysis = analysis.model_copy(update={"data_quality_flags": [flag], "contradicting_evidence": [event.id]})
    pack = build_evidence_pack(
        case=case, events=[event], source_files=[], analysis=analysis,
        correlation=correlate_events([event], case.id), generated_at=T0,
    )
    assert pack.timeline[0].data_quality_flags == ["SYNTHETIC_FLAG"]
    assert pack.evidence.supporting_event_ids == [event.id]
    assert pack.evidence.contradicting_event_ids == [event.id]
