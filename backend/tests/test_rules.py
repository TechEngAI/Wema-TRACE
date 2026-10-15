"""Comprehensive Deterministic Outcome Rules Engine Tests (Part 4).

Covers the ten required investigation scenarios, strength capping, duplicate
collapse, purity, determinism, and the analyze API endpoint.
"""

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import CaseModel, EventModel, RawRecordModel, SourceFileModel
from app.db.session import get_db
from app.main import app
from app.rules.engine import RULE_VERSION, evaluate_case
from app.schemas.enums import OutcomeEnum, StrengthEnum
from app.schemas.evidence import EvidencePackResponse

# Test DB Setup
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
def clean_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture(autouse=True)
def scoped_db_override():
    """Install this module's DB override only for this module's tests.

    The override is restored on teardown so other test modules keep their own
    (import-time) dependency wiring.
    """
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)


def make_case(case_id: uuid.UUID | None = None) -> CaseModel:
    cid = case_id or uuid.uuid4()
    return CaseModel(
        id=cid,
        reference=f"TRC-RULE-{str(cid)[:8]}",
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
    event_id: uuid.UUID | None = None,
) -> EventModel:
    return EventModel(
        id=event_id or uuid.uuid4(),
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


# -----------------------------------------------------------------------------
# Scenario 1: clean, authoritatively linked settlement -> NO_DISCREPANCY_FOUND/HIGH
# -----------------------------------------------------------------------------

def test_scenario_1_clean_settlement_no_discrepancy_high():
    case = make_case()
    sess = "SESS-CLEAN-1"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=sess),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=sess),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=sess),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.HIGH
    assert res.category == "SETTLED_CONSISTENT"
    assert len(res.timeline) == 3
    assert set(res.supporting_event_ids) == {e.id for e in events}
    assert res.rule_version == RULE_VERSION


# -----------------------------------------------------------------------------
# Scenario 2: downstream timeout, no credit -> LIKELY_ROOT_CAUSE/MODERATE
# -----------------------------------------------------------------------------

def test_scenario_2_downstream_timeout_likely_root_cause_moderate():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="SESS-TIMEOUT"),
        make_event(
            case.id, "SWITCH", "SWITCH_OUT", "TIMEOUT",
            timestamp=T0 + timedelta(seconds=1), session_id="SESS-TIMEOUT",
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "DOWNSTREAM_TIMEOUT_NO_SETTLEMENT"


# -----------------------------------------------------------------------------
# Scenario 3: origination-only evidence -> INSUFFICIENT_EVIDENCE/INSUFFICIENT
# -----------------------------------------------------------------------------

def test_scenario_3_origination_only_insufficient():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="SESS-ONLY"),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert res.strength == StrengthEnum.INSUFFICIENT
    assert res.category == "INSUFFICIENT_EVIDENCE"


# -----------------------------------------------------------------------------
# Scenario 4: authoritatively linked amount mismatch -> DISCREPANCY_CONFIRMED/HIGH
# -----------------------------------------------------------------------------

def test_scenario_4_linked_amount_mismatch_discrepancy_high():
    case = make_case()
    sess = "SESS-MISMATCH"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", amount_minor_units=8500000, session_id=sess),
        make_event(
            case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", amount_minor_units=8000000,
            timestamp=T0 + timedelta(seconds=2), session_id=sess,
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.DISCREPANCY_CONFIRMED
    assert res.strength == StrengthEnum.HIGH
    assert res.category == "SETTLEMENT_AMOUNT_MISMATCH"


# -----------------------------------------------------------------------------
# Scenario 5A: duplicate upload -> unchanged outcome, provenance preserved
# -----------------------------------------------------------------------------

def test_scenario_5a_duplicate_upload_preserves_outcome():
    case = make_case()
    sess = "SESS-DUP-1"
    original = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=sess)
    duplicate = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=sess)  # identical signature
    events = [
        original,
        duplicate,
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=sess),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=sess),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.HIGH
    # Duplicate collapsed to a single canonical leg.
    assert len(res.timeline) == 3
    codes = {f.flag_code for f in res.data_quality_flags}
    assert "DUPLICATE_EVIDENCE" in codes
    # The duplicate's provenance is recorded in the flag, not double-counted.
    dup_flag = next(f for f in res.data_quality_flags if f.flag_code == "DUPLICATE_EVIDENCE")
    canonical_ids = {ev.event_id for ev in res.timeline}
    expected_duplicate_ids = {original.id, duplicate.id} - canonical_ids
    assert set(dup_flag.affected_event_ids) == expected_duplicate_ids
    assert any(r.evidence_type == "SOURCE_FILE_REUPLOAD" for r in res.recommended_next_evidence)


# -----------------------------------------------------------------------------
# Scenario 5B: two real debits -> DISCREPANCY_CONFIRMED/MODERATE
# -----------------------------------------------------------------------------

def test_scenario_5b_two_real_debits_discrepancy_moderate():
    case = make_case()
    events = [
        make_event(
            case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", amount_minor_units=8500000,
            session_id="SESS-DEBIT-A", reference="TXN-A",
        ),
        make_event(
            case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", amount_minor_units=1200000,
            timestamp=T0 + timedelta(seconds=40), session_id="SESS-DEBIT-B", reference="TXN-B",
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.DISCREPANCY_CONFIRMED
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "MULTIPLE_UNREVERSED_DEBITS"
    assert set(res.supporting_event_ids) == {e.id for e in events}


# -----------------------------------------------------------------------------
# Scenario 6A: system reversal -> NO_DISCREPANCY_FOUND/MODERATE
# -----------------------------------------------------------------------------

def test_scenario_6a_system_reversal_no_discrepancy_moderate():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="SESS-REV"),
        make_event(
            case.id, "ORIGINATING_BANK", "REVERSAL_ATTEMPT", "SUCCESS",
            timestamp=T0 + timedelta(seconds=5), session_id="SESS-REV",
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "DEBIT_REVERSED_SETTLED"


# -----------------------------------------------------------------------------
# Scenario 6B: merchant refund (not system reversal) -> request missing evidence
# -----------------------------------------------------------------------------

def test_scenario_6b_merchant_refund_requests_missing_evidence():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="SESS-MREF"),
        make_event(
            case.id, "MERCHANT", "ACK", "SUCCESS",
            timestamp=T0 + timedelta(seconds=3), reference="MREF-1",
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert res.category == "MERCHANT_REFUND_UNVERIFIED"
    assert len(res.missing_evidence) >= 1
    assert res.missing_evidence[0].expected_party.value == "ORIGINATING_BANK"
    assert res.missing_evidence[0].expected_action.value == "REVERSAL_ATTEMPT"
    assert len(res.recommended_next_evidence) >= 1


# -----------------------------------------------------------------------------
# Scenario 7: out-of-order timestamps cap a clean settlement at MODERATE
# -----------------------------------------------------------------------------

def test_scenario_7_out_of_order_caps_high_to_moderate():
    case = make_case()
    sess = "SESS-SKEW"
    events = [
        # Receiving-bank credit is stamped BEFORE the upstream debit (within skew).
        make_event(
            case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS",
            timestamp=T0 - timedelta(seconds=2), session_id=sess,
        ),
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", timestamp=T0, session_id=sess),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=sess),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.NO_DISCREPANCY_FOUND
    assert res.strength == StrengthEnum.MODERATE  # HIGH capped by ambiguous ordering
    assert res.strength != StrengthEnum.HIGH
    assert "CLOCK_SKEW_AMBIGUOUS_ORDERING" in {f.flag_code for f in res.data_quality_flags}


# -----------------------------------------------------------------------------
# Scenario 8: similar amount/currency, no confirmed link -> INSUFFICIENT_EVIDENCE/LOW
#
# Documented exception (api-contract §1.2 / §5.2 rule H): the transaction
# conclusion is INSUFFICIENT, but the co-located amount/currency is a weak
# *directional* pattern of a possible linkage, which the LOW label describes.
# -----------------------------------------------------------------------------

def test_scenario_8_unlinked_similar_evidence_low():
    case = make_case()
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", amount_minor_units=500000, session_id="SESS-8A"),
        make_event(
            case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", amount_minor_units=500000,
            timestamp=T0 + timedelta(seconds=10), session_id="SESS-8B",
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    # Documented exception: LOW conveys the weak directional (possible-linkage) pattern.
    assert res.strength == StrengthEnum.LOW
    assert res.category == "UNLINKED_SIMILAR_EVIDENCE"
    assert len(res.alternative_explanations) >= 1
    # Absence is not proof: the missing link must be requested, not assumed.
    assert len(res.missing_evidence) >= 1
    assert len(res.recommended_next_evidence) >= 1


# -----------------------------------------------------------------------------
# Scenario 9: sender debit SUCCESS vs receiving-bank credit FAILED.
#
# These are DIFFERENT payment stages, so this is a consistent cross-stage
# sequence (a downstream failure), NOT a contradiction: the failed credit is
# affirmative evidence of the failure mechanism.
# -----------------------------------------------------------------------------

def test_scenario_9_cross_stage_failure_not_contradiction():
    case = make_case()
    sess = "SESS-CROSS-STAGE"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=sess),
        make_event(
            case.id, "RECEIVING_BANK", "CREDIT", "FAILED",
            timestamp=T0 + timedelta(seconds=2), session_id=sess,
        ),
    ]
    res = evaluate(events, case)
    assert res.outcome == OutcomeEnum.LIKELY_ROOT_CAUSE
    assert res.strength == StrengthEnum.MODERATE
    assert res.category == "DOWNSTREAM_FAILURE_NO_SETTLEMENT"
    assert len(res.contradicting_evidence) == 0
    failed_credit = events[1]
    assert failed_credit.id in res.supporting_evidence
    assert len(res.missing_evidence) >= 1
    assert len(res.recommended_next_evidence) >= 1


# -----------------------------------------------------------------------------
# Scenario 10: no usable evidence -> INSUFFICIENT_EVIDENCE/INSUFFICIENT
# -----------------------------------------------------------------------------

def test_scenario_10_minimal_fallback_insufficient():
    case = make_case()
    res = evaluate([], case)
    assert res.outcome == OutcomeEnum.INSUFFICIENT_EVIDENCE
    assert res.strength == StrengthEnum.INSUFFICIENT
    assert res.category == "INSUFFICIENT_EVIDENCE"
    assert res.timeline == []


# -----------------------------------------------------------------------------
# Determinism & purity
# -----------------------------------------------------------------------------

def test_determinism_independent_of_input_order():
    case = make_case()
    sess = "SESS-DET"
    events = [
        make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id=sess),
        make_event(case.id, "SWITCH", "SWITCH_IN", "SUCCESS", timestamp=T0 + timedelta(seconds=1), session_id=sess),
        make_event(case.id, "RECEIVING_BANK", "CREDIT", "SUCCESS", timestamp=T0 + timedelta(seconds=2), session_id=sess),
    ]
    r1 = evaluate_case(case=case, events=events)
    r2 = evaluate_case(case=case, events=[events[2], events[0], events[1]])
    d1 = r1.model_dump(mode="json")
    d2 = r2.model_dump(mode="json")
    assert d1 == d2


def test_engine_does_not_mutate_source_events():
    case = make_case()
    e = make_event(case.id, "ORIGINATING_BANK", "DEBIT", "SUCCESS", session_id="SESS-PURITY")
    before = (e.id, e.status, e.amount_minor_units, e.event_timestamp)
    evaluate_case(case=case, events=[e])
    assert (e.id, e.status, e.amount_minor_units, e.event_timestamp) == before


# -----------------------------------------------------------------------------
# API endpoint
# -----------------------------------------------------------------------------

def test_analyze_endpoint_unknown_case_returns_404():
    res = client.post(f"/api/v1/cases/{uuid.uuid4()}/analyze")
    assert res.status_code == 404


def test_analyze_endpoint_returns_deterministic_contract():
    case_res = client.post(
        "/api/v1/cases",
        json={
            "reference": "TRC-RULE-API-01",
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
        "2026-10-14T22:15:30+01:00,TXN-API-R1,8500000,NGN,DEBIT,SUCCESS,SESS-API-1,004819204812\n"
    ).encode("utf-8")
    up = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("rules.csv", csv_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert up.status_code == 201

    r1 = client.post(f"/api/v1/cases/{case_id}/analyze")
    r2 = client.post(f"/api/v1/cases/{case_id}/analyze")
    assert r1.status_code == 200 and r2.status_code == 200

    d1, d2 = r1.json(), r2.json()
    assert d1["case_id"] == case_id
    assert d1["rule_version"] == RULE_VERSION
    assert d1["outcome"] in {o.value for o in OutcomeEnum}
    assert d1["strength"] in {s.value for s in StrengthEnum}
    for field in (
        "outcome_reason",
        "strength_reason",
        "scope_examined",
        "supporting_evidence",
        "contradicting_evidence",
        "alternative_explanations",
        "recommended_next_evidence",
        "explanation",
    ):
        assert field in d1
    assert d1["explanation"] is not None
    assert len(d1["explanation"]["summary"].split()) <= 80
    # Compute-on-demand determinism.
    assert d1 == d2


def test_evidence_pack_endpoint_preserves_analysis_and_validates_schema():
    case_res = client.post(
        "/api/v1/cases",
        json={
            "reference": "TRC-PACK-API-01",
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
        "2026-10-14T22:15:30+01:00,TXN-PACK-1,8500000,NGN,DEBIT,SUCCESS,SESS-PACK-1,004819204813\n"
    ).encode("utf-8")
    assert client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("pack.csv", csv_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    ).status_code == 201

    before = client.post(f"/api/v1/cases/{case_id}/analyze")
    pack_response = client.get(f"/api/v1/cases/{case_id}/evidence-pack")
    after = client.post(f"/api/v1/cases/{case_id}/analyze")
    assert before.status_code == 200 and pack_response.status_code == 200 and after.status_code == 200
    pack = EvidencePackResponse.model_validate(pack_response.json())
    assert pack.case_id == uuid.UUID(case_id)
    assert pack.summary.outcome.value == before.json()["outcome"] == after.json()["outcome"]
    assert pack.summary.strength.value == before.json()["strength"] == after.json()["strength"]
    assert pack.explanation.model_dump(mode="json") == before.json()["explanation"]
    assert pack.integrity.sha256
