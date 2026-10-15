"""Adversarial release-readiness audit regression suite for the Part 4 engine.

These tests encode the adversarial inputs from the release audit (Audits 1-6)
and lock the corrected, defensible behaviour.  They are intentionally kept in a
separate module from ``test_rules.py`` so the scenario suite stays readable.

Every test here corresponds to a concrete audit obligation:
- Audit 1: contradictory / precedence scenarios.
- Audit 2: identity vs proximity and separation of evidence.
- Audit 3: duplicate vs genuine-debit detection.
- Audit 4: strength caps and the INSUFFICIENT invariant.
- Audit 5: actionable next evidence for INSUFFICIENT_EVIDENCE.
- Audit 6: determinism, purity, and the analyze API.
"""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.correlation.exceptions import CrossCaseCorrelationError
from app.db.base import Base
from app.db.models import CaseModel, EventModel, RawRecordModel, SourceFileModel
from app.db.session import get_db
from app.main import app
from app.rules.engine import RULE_VERSION, evaluate_case
from app.schemas.enums import OutcomeEnum, StrengthEnum

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)
T0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def scoped_db_override():
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)


def make_case() -> CaseModel:
    cid = uuid.uuid4()
    return CaseModel(
        id=cid,
        reference=f"TRC-AUD-{str(cid)[:8]}",
        initiating_party="ORIGINATING_BANK",
        counterparty="RECEIVING_BANK",
        disputed_amount=8500000,
        currency="NGN",
        status="OPEN",
        session_id=None,
        rrn=None,
        narrative=None,
        metadata_json={},
        created_at=datetime(2026, 10, 14, 22, 15, 0, tzinfo=timezone.utc),
    )


def make_event(
    case_id: uuid.UUID,
    party: str,
    action: str,
    status: str,
    amount_minor_units: int = 8500000,
    currency: str = "NGN",
    timestamp: datetime = T0,
    session_id: str | None = None,
    rrn: str | None = None,
    reference: str | None = None,
) -> EventModel:
    return EventModel(
        id=uuid.uuid4(),
        case_id=case_id,
        raw_record_id=uuid.uuid4(),
        party=party,
        action=action,
        status=status,
        amount_minor_units=amount_minor_units,
        currency=currency,
        session_id=session_id,
        rrn=rrn,
        reference=reference,
        channel="MOBILE",
        event_timestamp=timestamp,
        metadata_json={},
    )


def evaluate(events, case=None):
    case = case or make_case()
    return evaluate_case(case=case, events=events)


def flag_codes(res):
    return {f.flag_code for f in res.data_quality_flags}


# =============================================================================
# Audit 1 - contradictory / precedence scenarios
# =============================================================================

def test_a1a_amount_mismatch_with_conflicting_statuses_conflicting_wins():
    """A confirmed chain with contradictory terminal statuses beats amount mismatch.

    Rationale: if the linkage claims both SUCCESS and FAILED for the same
    authoritative cluster, the chain is unreliable and an amount comparison
    within it cannot confirm a settlement discrepancy.
    """
    case = make_case()
    s = "A1A"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_IN", "FAILED", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", 8000000, timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.CONFLICTING_EVIDENCE
    assert res.strength == StrengthEnum.LOW
    assert res.category == "CONTRADICTORY_TERMINAL_STATUS"


def test_a1b_timeout_with_receiving_bank_failure_same_session_surfaces_failure():
    case = make_case()
    s = "A1B"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    # Debit (send) SUCCESS + credit (settlement) FAILED are different legs: this
    # is a cross-stage failure, not a contradiction.
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.category == "DOWNSTREAM_TIMEOUT_NO_SETTLEMENT"
    assert events[2].id in res.supporting_evidence  # FAILED credit preserved


def test_a1b2_timeout_with_receiving_bank_failure_distinct_sessions_surfaces_failed_credit():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="A1B2-A"),
        make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=1), session_id="A1B2-A"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id="A1B2-B"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "DOWNSTREAM_TIMEOUT_NO_SETTLEMENT"
    failed_credit = events[2]
    assert failed_credit.id in res.supporting_evidence  # affirmative failure evidence is kept


def test_a1c_reversal_with_contradictory_merchant_success_surfaces_contradiction():
    case = make_case()
    s = "A1C"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "ORIGINATING_BANK", "REVERSAL_ATTEMPT", "SUCCESS", timestamp=T0 + timedelta(seconds=3), session_id=s),
        make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=4), session_id=s, reference="M-A1C"),
    ]
    res = evaluate(events, case)
    # Funds cannot be both reversed to the sender AND paid to the merchant: the
    # two material claims are genuinely incompatible.
    assert res.outcome == OutcomeEnum.CONFLICTING_EVIDENCE
    assert res.strength == StrengthEnum.LOW
    assert res.category == "REVERSAL_SETTLEMENT_CONFLICT"
    merchant = events[2]
    assert merchant.id in res.contradicting_evidence  # contradictory downstream success is surfaced


def test_a1d_duplicate_debit_candidate_with_unresolved_identity_not_confirmed():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, session_id="A1D-A", reference="REF-A1D"),
        # Same amount/time/reference but the session id is absent -> identity unresolved.
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, session_id=None, reference="REF-A1D"),
    ]
    res = evaluate(events, case)
    assert res.outcome != OutcomeEnum.DISCREPANCY_CONFIRMED
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    # Rule H documented exception: LOW expresses the weak directional duplicate pattern.
    assert res.strength == StrengthEnum.LOW


def test_a1e_clean_settlement_with_material_flag_capped_at_moderate():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="CLEAN-A1E"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id="CLEAN-A1E"),
        # A separate, mutually inconsistent identifier cluster (material flag).
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=10), session_id="CONF", rrn="R1"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=11), session_id="CONF", rrn="R2"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.MODERATE
    assert res.strength != StrengthEnum.HIGH
    assert "IDENTIFIER_CONFLICT" in flag_codes(res)


def test_a1f_likely_root_cause_with_contradictory_downstream_success():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="A1F-A"),
        make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=1), session_id="A1F-A"),
        # A downstream SUCCESS (merchant confirms payment) contradicts the timeout
        # and must be surfaced; it is not itself part of the upstream chain.
        make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=2), reference="M-A1F"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    downstream = events[2]
    assert downstream.id in res.contradicting_evidence


# =============================================================================
# Audit 2 - identity vs proximity; evidence separation
# =============================================================================

def test_a2a_identical_amount_and_close_time_distinct_sessions_not_confirmed():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 500000, session_id="A2A-A"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", 500000, timestamp=T0 + timedelta(seconds=2), session_id="A2A-B"),
    ]
    res = evaluate(events, case)
    assert res.outcome != OutcomeEnum.DISCREPANCY_CONFIRMED
    assert res.outcome != OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE


def test_a2b_cross_case_events_rejected():
    case = make_case()
    other = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="A2B"),
        make_event(other.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="A2B"),
    ]
    with pytest.raises(CrossCaseCorrelationError):
        evaluate_case(case=case, events=events)


def test_a2c_conflicting_session_id_flagged_critical_not_high():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="A2C-A", rrn="RRN-A2C"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="A2C-B", rrn="RRN-A2C"),
    ]
    res = evaluate(events, case)
    assert "IDENTIFIER_CONFLICT" in flag_codes(res)
    assert res.strength != StrengthEnum.HIGH
    assert res.outcome != OutcomeEnum.DISCREPANCY_CONFIRMED


def test_a2c2_conflicting_currency_flagged_critical():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", currency="NGN", rrn="RRN-A2C2"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), currency="USD", rrn="RRN-A2C2"),
    ]
    res = evaluate(events, case)
    assert "CURRENCY_CONFLICT" in flag_codes(res)
    assert res.strength != StrengthEnum.HIGH


def test_a2d_all_evidence_ids_are_stored_and_disjoint():
    case = make_case()
    s = "A2D"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_IN", "FAILED", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    input_ids = {e.id for e in events}
    supporting = set(res.supporting_evidence)
    contradicting = set(res.contradicting_evidence)
    assert supporting <= input_ids
    assert contradicting <= input_ids
    assert supporting.isdisjoint(contradicting)
    assert set(res.supporting_event_ids) <= input_ids


# =============================================================================
# Audit 3 - duplicate vs genuine debit detection
# =============================================================================

def test_a3a_exact_duplicate_upload_not_double_counted():
    case = make_case()
    s = "A3A"
    original = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s)
    duplicate = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s)
    events = [
        original,
        duplicate,
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.HIGH
    assert "DUPLICATE_EVIDENCE" in flag_codes(res)
    assert len(res.timeline) == 3  # duplicate collapsed


def test_a3b_two_genuine_independent_debits_confirmed():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, session_id="A3B-A", reference="A3B-1"),
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 1200000, timestamp=T0 + timedelta(seconds=60), session_id="A3B-B", reference="A3B-2"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.DISCREPANCY_CONFIRMED
    assert res.category == "MULTIPLE_UNREVERSED_DEBITS"
    assert set(res.supporting_event_ids) == {e.id for e in events}


def test_a3c_similar_signature_insufficient_identifiers_not_confirmed():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, session_id="A3C-A", reference="A3C"),
        # Near-duplicate: same amount/reference but only an ambiguous partial identity.
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 8500000, timestamp=T0 + timedelta(seconds=2), session_id=None, reference="A3C"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    # Rule H documented exception: LOW expresses the weak directional duplicate pattern.
    assert res.strength == StrengthEnum.LOW


# =============================================================================
# Audit 4 - strength caps and the INSUFFICIENT invariant
# =============================================================================

def test_a4a_high_requires_confirmed_link_and_no_flags():
    case = make_case()
    s = "A4A"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.strength == StrengthEnum.HIGH
    assert res.data_quality_flags == []
    assert "no capping data-quality flags" in res.strength_reason


def test_a4b_critical_flag_caps_high_and_records_reason():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="CLEAN-A4B"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id="CLEAN-A4B"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=10), session_id="C4B", rrn="R1"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=11), session_id="C4B", rrn="R2"),
    ]
    res = evaluate(events, case)
    assert res.strength == StrengthEnum.MODERATE
    assert "Capped at MODERATE" in res.strength_reason


def test_a4c_low_flag_never_caps():
    case = make_case()
    s = "A4C"
    original = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s)
    duplicate = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s)
    events = [
        original,
        duplicate,
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert "DUPLICATE_EVIDENCE" in flag_codes(res)
    assert res.strength == StrengthEnum.HIGH  # LOW flag does not cap


def test_a4d_cap_never_raises_strength():
    case = make_case()
    # Rule A yields LOW (same-leg contradiction); an unrelated CRITICAL ambiguity
    # must not raise it.
    s = "A4D"
    events = [
        # Same INBOUND leg reported with conflicting terminal statuses.
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=10), session_id="X", rrn="R1"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=11), session_id="X", rrn="R2"),
    ]
    res = evaluate(events, case)
    assert "IDENTIFIER_CONFLICT" in flag_codes(res)
    assert res.outcome == OutcomeEnum.CONFLICTING_EVIDENCE
    assert res.strength == StrengthEnum.LOW


def test_a4e_insufficient_evidence_invariant_holds_for_all_paths():
    # E2: merchant refund without reversal
    case = make_case()
    e2 = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="E2"),
        make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=3), reference="M-E2"),
    ]
    # G: downstream success but no settlement
    g = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="G"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="G"),
    ]
    # H: unlinked similar evidence
    h = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 500000, session_id="H-A"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", 500000, timestamp=T0 + timedelta(seconds=2), session_id="H-B"),
    ]
    # I: fallback (origination only)
    i = [make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="I")]
    expected = {
        "E2": StrengthEnum.INSUFFICIENT,
        "G": StrengthEnum.INSUFFICIENT,
        # H documented exception: LOW conveys a weak directional pattern.
        "H": StrengthEnum.LOW,
        "I": StrengthEnum.INSUFFICIENT,
    }
    for label, events in (("E2", e2), ("G", g), ("H", h), ("I", i)):
        res = evaluate(events, case)
        assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE, label
        assert res.strength == expected[label], label


# =============================================================================
# Audit 5 - actionable next evidence for INSUFFICIENT_EVIDENCE
# =============================================================================

def test_a5_every_insufficient_finding_is_actionable():
    case = make_case()
    e2 = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="E2R"),
        make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=3), reference="M-E2R"),
    ]
    g = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="GR"),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="GR"),
    ]
    h = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 500000, session_id="HR-A"),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", 500000, timestamp=T0 + timedelta(seconds=2), session_id="HR-B"),
    ]
    i = [make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="IR")]
    for label, events in (("E2", e2), ("G", g), ("H", h), ("I", i)):
        res = evaluate(events, case)
        assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE, label
        assert len(res.missing_evidence) >= 1, label
        assert len(res.recommended_next_evidence) >= 1, label
        assert all(item.evidence_type for item in res.missing_evidence), label
        assert all(item.rationale for item in res.recommended_next_evidence), label


# =============================================================================
# Audit 6 - determinism, purity, and the analyze API
# =============================================================================

def test_a6a_deterministic_regardless_of_input_order():
    case = make_case()
    s = "A6"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_IN", "FAILED", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    r1 = evaluate_case(case=case, events=events).model_dump(mode="json")
    r2 = evaluate_case(case=case, events=list(reversed(events))).model_dump(mode="json")
    assert r1 == r2


def test_a6b_engine_is_pure():
    case = make_case()
    e = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="PURE")
    before = (e.id, e.status, e.amount_minor_units, e.event_timestamp, e.session_id)
    evaluate_case(case=case, events=[e])
    assert (e.id, e.status, e.amount_minor_units, e.event_timestamp, e.session_id) == before


def test_a6c_rule_version_is_always_stamped():
    case = make_case()
    res = evaluate([make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="V")], case)
    assert res.rule_version == RULE_VERSION


def test_a6d_analyze_api_unknown_case_404_and_deterministic():
    assert client.post(f"/api/v1/cases/{uuid.uuid4()}/analyze").status_code == 404

    case_res = client.post(
        "/api/v1/cases",
        json={
            "reference": "TRC-AUD-API-01",
            "initiating_party": "ORIGINATING_BANK",
            "counterparty": "RECEIVING_BANK",
            "disputed_amount": 8500000,
            "currency": "NGN",
        },
    )
    assert case_res.status_code == 201
    case_id = case_res.json()["case_id"]

    csv_payload = (
        "timestamp,reference,amount,currency,action,status,session_id,rrn\n"
        "2026-10-14T22:15:30+01:00,TXN-AUD-R1,8500000,NGN,DEBIT,SUCCESS,SESS-AUD-1,004819204812\n"
    ).encode("utf-8")
    up = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("audit.csv", csv_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert up.status_code == 201

    r1 = client.post(f"/api/v1/cases/{case_id}/analyze")
    r2 = client.post(f"/api/v1/cases/{case_id}/analyze")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["strength"] in {s.value for s in StrengthEnum}


# =============================================================================
# Issue 1 - payment-stage distinction: different stages are NOT contradictions
#
# Six regression cases establishing that the engine distinguishes a linked
# cross-stage failure sequence (affirmative failure evidence) from a genuine
# same-stage contradiction, and from a retry/recovery that cannot be linked.
# =============================================================================

def test_issue1_case1_different_stages_failed_credit_is_not_contradiction():
    """Sender debit SUCCESS + receiving-bank credit FAILED = cross-stage failure."""
    case = make_case()
    s = "ISS1-C1"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "DOWNSTREAM_FAILURE_NO_SETTLEMENT"
    assert res.contradicting_evidence == []
    assert events[1].id in res.supporting_evidence  # failed credit preserved
    assert len(res.missing_evidence) >= 1
    assert len(res.recommended_next_evidence) >= 1


def test_issue1_case2_gateway_success_failed_credit_is_not_contradiction():
    """Gateway (switch-out) SUCCESS + credit FAILED = cross-stage failure."""
    case = make_case()
    s = "ISS1-C2"
    events = [
        make_event(case.id, "SWITCH", "SWITCH_OUT", "SUCCESS", session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.category == "DOWNSTREAM_FAILURE_NO_SETTLEMENT"
    assert res.contradicting_evidence == []


def test_issue1_case3_same_stage_conflict_is_contradiction():
    """Conflicting terminal statuses on the SAME leg = genuine contradiction."""
    case = make_case()
    s = "ISS1-C3"
    events = [
        # Both describe the inbound/settlement leg, but with opposed terminal statuses.
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.CONFLICTING_EVIDENCE
    assert res.strength == StrengthEnum.LOW
    assert res.category == "CONTRADICTORY_TERMINAL_STATUS"


def test_issue1_case4_retry_under_different_identity_not_confirmed():
    """A failed attempt and an independent successful retry under a different
    identity cannot be authoritatively linked."""
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="ISS1-C4-A", reference="ISS1-C4-A"),
        # Failed attempt under a distinct identity.
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id="ISS1-C4-B", reference="ISS1-C4-B"),
        # Independent successful retry under another distinct identity.
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=30), session_id="ISS1-C4-C", reference="ISS1-C4-C"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert res.outcome != OutcomeEnum.DISCREPANCY_CONFIRMED
    # The retry/recovery possibility must be stated, not silently assumed away.
    assert any("retried" in alt.lower() or "recover" in alt.lower() for alt in res.alternative_explanations)


def test_issue1_case5_timeout_then_verified_settlement_is_recovery():
    """A downstream timeout followed by verified settlement is a recovery."""
    case = make_case()
    s = "ISS1-C5"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=1), session_id=s),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=s),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.HIGH


def test_issue1_case6_reversal_with_downstream_payment_is_contradiction():
    """Reversal (funds returned) + merchant paid = genuinely incompatible claims."""
    case = make_case()
    s = "ISS1-C6"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=s),
        make_event(case.id, "ORIGINATING_BANK", "REVERSAL_ATTEMPT", "SUCCESS", timestamp=T0 + timedelta(seconds=3), session_id=s),
        make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=4), session_id=s, reference="M-ISS1-C6"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.CONFLICTING_EVIDENCE
    assert res.strength == StrengthEnum.LOW
    assert res.category == "REVERSAL_SETTLEMENT_CONFLICT"
    assert events[2].id in res.contradicting_evidence


# =============================================================================
# Part 4 final linkage-safety regressions: D / D2 / D3
# =============================================================================

def assert_unresolved_linkage_result(result, expected_supporting):
    """Assert the complete contract for an unconfirmed D/D2/D3 relationship."""
    assert result.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert result.strength == StrengthEnum.INSUFFICIENT
    assert result.category == "UNRESOLVED_TRANSACTION_LINKAGE"
    assert set(result.supporting_evidence) == set(expected_supporting)
    assert result.contradicting_evidence == []
    assert [item.evidence_type for item in result.missing_evidence] == ["CORRELATION_IDENTIFIER"]
    assert [item.evidence_type for item in result.recommended_next_evidence] == ["CORRELATION_IDENTIFIER"]
    assert set(result.recommended_next_evidence[0].related_event_ids) == set(expected_supporting)


def test_linkage_safety_unrelated_confirmed_debit_and_timeout_are_not_attributed():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="LS-1A")
    credit = make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="LS-1A")
    timeout = make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=2), session_id="LS-1B")
    result = evaluate([debit, credit, timeout], case)
    assert_unresolved_linkage_result(result, [debit.id, timeout.id])
    assert timeout.id in {item.event_id for item in result.timeline}


def test_linkage_safety_unrelated_confirmed_debit_and_failed_credit_are_not_attributed():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="LS-2A")
    credit = make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="LS-2A")
    failed = make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=2), session_id="LS-2B")
    result = evaluate([debit, credit, failed], case)
    assert_unresolved_linkage_result(result, [debit.id, failed.id])
    assert failed.id in {item.event_id for item in result.timeline}


def test_linkage_safety_confirmed_debit_timeout_remains_likely_root_cause():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="LS-3")
    timeout = make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", timestamp=T0 + timedelta(seconds=1), session_id="LS-3")
    result = evaluate([debit, timeout], case)
    assert result.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert result.strength == StrengthEnum.MODERATE
    assert set(result.supporting_evidence) == {debit.id, timeout.id}
    assert result.contradicting_evidence == []
    assert [item.evidence_type for item in result.missing_evidence] == ["SETTLEMENT_RESOLUTION"]
    assert [item.evidence_type for item in result.recommended_next_evidence] == ["SETTLEMENT_RESOLUTION"]


def test_linkage_safety_confirmed_debit_failed_credit_remains_likely_root_cause():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="LS-4")
    failed = make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=1), session_id="LS-4")
    result = evaluate([debit, failed], case)
    assert result.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert result.strength == StrengthEnum.MODERATE
    assert set(result.supporting_evidence) == {debit.id, failed.id}
    assert result.contradicting_evidence == []
    assert [item.evidence_type for item in result.missing_evidence] == ["REVERSAL_RECORD"]
    assert [item.evidence_type for item in result.recommended_next_evidence] == ["REVERSAL_RECORD"]


def test_linkage_safety_candidate_only_debit_failed_credit_is_insufficient():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS")
    failed = make_event(case.id, "RECEIVING_BANK", "CREDIT", "FAILED", timestamp=T0 + timedelta(seconds=1))
    result = evaluate([debit, failed], case)
    assert_unresolved_linkage_result(result, [debit.id, failed.id])


def test_linkage_safety_ambiguous_debit_reversal_is_not_attributed_to_downstream_value():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="LS-6A", rrn="LS-6")
    reversal = make_event(case.id, "ORIGINATING_BANK", "REVERSAL_ATTEMPT", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id="LS-6B", rrn="LS-6")
    paid = make_event(case.id, "MERCHANT", "ACK", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id="LS-6C")
    result = evaluate([debit, reversal, paid], case)
    assert_unresolved_linkage_result(result, [debit.id, reversal.id, paid.id])
    assert "IDENTIFIER_CONFLICT" in flag_codes(result)


def test_linkage_safety_missing_identifiers_and_similar_amounts_do_not_link_timeout():
    case = make_case()
    debit = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", 77700)
    timeout = make_event(case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT", 77700, timestamp=T0 + timedelta(seconds=1))
    result = evaluate([debit, timeout], case)
    assert_unresolved_linkage_result(result, [debit.id, timeout.id])
