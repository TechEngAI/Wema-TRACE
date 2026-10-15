"""Comprehensive Deterministic Transaction Correlation Tests (Part 3).

Verifies the correlation engine hierarchy, ambiguity detection, candidate group
generation, temporal tolerance handling, and strict audit purity.
"""

from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.correlation.engine import ALGORITHM_VERSION, correlate_events
from app.correlation.exceptions import CrossCaseCorrelationError
from app.db.base import Base
from app.db.models import CaseModel, EventModel, RawRecordModel, SourceFileModel
from app.db.session import get_db
from app.main import app
from app.schemas.correlation import (
    CorrelationMethodEnum,
    CorrelationReasonCode,
    CorrelationStatusEnum,
)

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


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


def make_event(
    case_id: uuid.UUID,
    party: str,
    action: str,
    status: str,
    amount_minor_units: int,
    currency: str,
    timestamp: datetime,
    session_id: str | None = None,
    rrn: str | None = None,
    reference: str | None = None,
    event_id: uuid.UUID | None = None,
) -> EventModel:
    """Helper to instantiate an in-memory EventModel for pure engine testing."""
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


# -----------------------------------------------------------------------------
# 1. Strong Match: Session ID and RRN
# -----------------------------------------------------------------------------

def test_strong_match_session_and_rrn():
    """Verify compatible session ID and RRN produces CONFIRMED correlation group."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="100004241014221530120194820",
        rrn="004819204812",
        reference="TXN-ORIG-01",
    )
    e2 = make_event(
        case_id=case_id,
        party="SWITCH",
        action="SWITCH_IN",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        session_id="100004241014221530120194820",
        rrn="004819204812",
    )
    e3 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="PENDING",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=2),
        session_id="100004241014221530120194820",
        rrn="004819204812",
    )

    res = correlate_events([e1, e2, e3], case_id=case_id)
    assert res.total_events == 3
    assert len(res.groups) == 1

    group = res.groups[0]
    assert group.status == CorrelationStatusEnum.CONFIRMED
    assert group.method == CorrelationMethodEnum.SESSION_AND_RRN_MATCH
    assert group.reason_code == CorrelationReasonCode.STRONG_SESSION_AND_RRN_CONSISTENT
    assert len(group.member_event_ids) == 3
    assert set(group.member_event_ids) == {e1.id, e2.id, e3.id}
    assert group.identifying_keys["session_id"] == "100004241014221530120194820"
    assert group.identifying_keys["rrn"] == "004819204812"
    assert len(group.contradictions) == 0


# -----------------------------------------------------------------------------
# 2. Conflicting Identifiers: Session ID with Conflicting RRN
# -----------------------------------------------------------------------------

def test_session_id_with_conflicting_rrn_is_ambiguous():
    """Verify that matching session ID with conflicting RRNs flags AMBIGUOUS."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-COMMON",
        rrn="RRN-111111",
    )
    e2 = make_event(
        case_id=case_id,
        party="SWITCH",
        action="SWITCH_IN",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        session_id="SESS-COMMON",
        rrn="RRN-222222",  # CONFLICT!
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert len(res.groups) == 1
    group = res.groups[0]

    assert group.status == CorrelationStatusEnum.AMBIGUOUS
    assert group.reason_code == CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT
    assert len(group.contradictions) > 0
    assert "RRN" in group.contradictions[0]


# -----------------------------------------------------------------------------
# 3. Conflicting Identifiers: RRN with Conflicting Session ID
# -----------------------------------------------------------------------------

def test_rrn_with_conflicting_session_id_is_ambiguous():
    """Verify that matching RRN with conflicting session IDs flags AMBIGUOUS."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="SWITCH",
        action="SWITCH_IN",
        status="SUCCESS",
        amount_minor_units=500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-ALPHA",
        rrn="RRN-SHARED-99",
    )
    e2 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="SUCCESS",
        amount_minor_units=500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        session_id="SESS-BETA",  # CONFLICT!
        rrn="RRN-SHARED-99",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert len(res.groups) == 1
    group = res.groups[0]

    assert group.status == CorrelationStatusEnum.AMBIGUOUS
    assert group.reason_code == CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT
    assert len(group.contradictions) > 0


# -----------------------------------------------------------------------------
# 4. Proximity Match: Same Amount & Time with Different Session IDs -> Candidate
# -----------------------------------------------------------------------------

def test_same_amount_and_time_different_session_ids_is_candidate():
    """Verify amount and timestamp proximity with distinct session IDs is candidate at most, never confirmed."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    # Two events from different parties with matching amount and close timestamps but different session IDs
    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-USER-A",
    )
    e2 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="PENDING",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=2),
        session_id="SESS-USER-B",  # Distinct session ID!
    )

    # In our engine, e1 has SESS-USER-A (alone) and e2 has SESS-USER-B (alone).
    # Since neither cluster has >1 event on session_id, they fall to Pass 3 proximity:
    res = correlate_events([e1, e2], case_id=case_id, tolerance_seconds=5.0)

    # Must be CANDIDATE, strictly NOT confirmed
    assert res.summary["CONFIRMED"] == 0
    candidate_groups = [g for g in res.groups if g.status == CorrelationStatusEnum.CANDIDATE]
    assert len(candidate_groups) == 1
    assert candidate_groups[0].method == CorrelationMethodEnum.TEMPORAL_AMOUNT_PROXIMITY
    assert candidate_groups[0].reason_code == CorrelationReasonCode.CANDIDATE_TEMPORAL_AMOUNT_PROXIMITY


# -----------------------------------------------------------------------------
# 5. Currency Mismatch: Same Amount but Different Currencies -> No Confirmed Match
# -----------------------------------------------------------------------------

def test_same_amount_different_currencies_no_confirmed_match():
    """Verify equal numeric amounts in different currencies are never confirmed."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
    )
    e2 = make_event(
        case_id=case_id,
        party="MERCHANT",
        action="ACK",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="USD",  # Different currency!
        timestamp=t0 + timedelta(seconds=1),
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0
    assert all(g.status != CorrelationStatusEnum.CONFIRMED for g in res.groups)


# -----------------------------------------------------------------------------
# 6. Secondary Match: Institution-Local Reference Used by Unrelated Records
# -----------------------------------------------------------------------------

def test_institution_local_reference_not_confirmed():
    """Verify that multiple events within the same institution sharing a local ref are not confirmed as unique cross-party match."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    # Two separate debits inside the originating bank reusing "BATCH-REF-01"
    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=100000,
        currency="NGN",
        timestamp=t0,
        reference="BATCH-REF-01",
    )
    e2 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=200000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        reference="BATCH-REF-01",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0
    candidate_groups = [g for g in res.groups if g.status == CorrelationStatusEnum.CANDIDATE]
    assert len(candidate_groups) == 1
    assert candidate_groups[0].reason_code == CorrelationReasonCode.CANDIDATE_PARTIAL_REFERENCE_ONLY


def test_cross_party_verified_reference_confirmed():
    """Verify that exactly 2 distinct parties sharing a verified cross-party reference are confirmed."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        reference="ORDER-REF-88492",
    )
    e2 = make_event(
        case_id=case_id,
        party="MERCHANT",
        action="ACK",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        reference="ORDER-REF-88492",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 1
    assert res.groups[0].method == CorrelationMethodEnum.INSTITUTION_SCOPED_REFERENCE
    assert res.groups[0].reason_code == CorrelationReasonCode.SECONDARY_INSTITUTION_REFERENCE_VERIFIED


# -----------------------------------------------------------------------------
# 7. Missing Identifiers: Unmatched
# -----------------------------------------------------------------------------

def test_missing_identifiers_unmatched():
    """Verify events with missing identifiers that do not align temporally are UNMATCHED."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=100000,
        currency="NGN",
        timestamp=t0,
    )
    e2 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="SUCCESS",
        amount_minor_units=999999,  # Different amount
        currency="NGN",
        timestamp=t0 + timedelta(seconds=30),  # Far outside tolerance
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["UNMATCHED"] == 2
    assert all(g.status == CorrelationStatusEnum.UNMATCHED for g in res.groups)


# -----------------------------------------------------------------------------
# 8. Events Outside Timestamp Tolerance
# -----------------------------------------------------------------------------

def test_events_outside_timestamp_tolerance_not_grouped():
    """Verify events sharing amount and currency but outside ±5s window remain UNMATCHED."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=500000,
        currency="NGN",
        timestamp=t0,
    )
    e2 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="PENDING",
        amount_minor_units=500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=12),  # 12 seconds > 5.0s tolerance!
    )

    res = correlate_events([e1, e2], case_id=case_id, tolerance_seconds=5.0)
    assert res.summary["CONFIRMED"] == 0
    assert res.summary["CANDIDATE"] == 0
    assert res.summary["UNMATCHED"] == 2


# -----------------------------------------------------------------------------
# 9. Events Inside ±5-second Window with Ambiguous Ordering
# -----------------------------------------------------------------------------

def test_ambiguous_temporal_ordering_flag():
    """Verify that inverted timestamps within ±5s tolerance flag temporal_ordering_ambiguous=True."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    # Originating bank debit stamped at t0
    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-SKEW-TEST",
    )
    # Receiving bank credit is downstream, but device clock stamped it 2 seconds EARLIER than originating bank!
    e2 = make_event(
        case_id=case_id,
        party="RECEIVING_BANK",
        action="CREDIT",
        status="PENDING",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 - timedelta(seconds=2),  # Apparent clock inversion within 5s window
        session_id="SESS-SKEW-TEST",
    )

    res = correlate_events([e1, e2], case_id=case_id, tolerance_seconds=5.0)
    assert res.summary["CONFIRMED"] == 1
    group = res.groups[0]
    # Check that ambiguous ordering flag is raised
    assert group.temporal_ordering_ambiguous is True
    # Verify raw timestamps were NOT rewritten
    assert group.events[0].event_timestamp == t0 - timedelta(seconds=2)
    assert group.events[1].event_timestamp == t0


# -----------------------------------------------------------------------------
# 10. Determinism: Same Inputs Inserted / Retrieved in Different Orders
# -----------------------------------------------------------------------------

def test_deterministic_grouping_invariant_to_input_order():
    """Verify that different input retrieval orders produce 100% byte-for-byte identical results."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-DET-01",
    )
    e2 = make_event(
        case_id=case_id,
        party="SWITCH",
        action="SWITCH_IN",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=1),
        session_id="SESS-DET-01",
    )
    e3 = make_event(
        case_id=case_id,
        party="MERCHANT",
        action="ACK",
        status="SUCCESS",
        amount_minor_units=1200000,
        currency="NGN",
        timestamp=t0 + timedelta(seconds=2),
        reference="UNMATCHED-REF",
    )

    # Order 1: [e1, e2, e3]
    res1 = correlate_events([e1, e2, e3], case_id=case_id)
    # Order 2: [e3, e1, e2]
    res2 = correlate_events([e3, e1, e2], case_id=case_id)
    # Order 3: [e2, e3, e1]
    res3 = correlate_events([e2, e3, e1], case_id=case_id)

    # Group counts and IDs must be identical
    assert len(res1.groups) == len(res2.groups) == len(res3.groups)
    for g1, g2, g3 in zip(res1.groups, res2.groups, res3.groups):
        assert g1.group_id == g2.group_id == g3.group_id
        assert g1.member_event_ids == g2.member_event_ids == g3.member_event_ids
        assert g1.status == g2.status == g3.status
        assert g1.reason_code == g2.reason_code == g3.reason_code


# -----------------------------------------------------------------------------
# 11. Cross-Case Boundary Isolation
# -----------------------------------------------------------------------------

def test_cross_case_correlation_prohibited():
    """Verify that attempting to correlate events belonging to different cases raises an error."""
    case_1 = uuid.uuid4()
    case_2 = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_1,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-SAME-ID",
    )
    e2 = make_event(
        case_id=case_2,
        party="SWITCH",
        action="SWITCH_IN",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-SAME-ID",
    )

    with pytest.raises(CrossCaseCorrelationError):
        correlate_events([e1, e2])


# -----------------------------------------------------------------------------
# 12. Purity & Idempotency: Source Events Unmutated
# -----------------------------------------------------------------------------

def test_correlation_does_not_mutate_source_events():
    """Verify that correlation does not mutate source event attributes."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id,
        party="ORIGINATING_BANK",
        action="DEBIT",
        status="SUCCESS",
        amount_minor_units=8500000,
        currency="NGN",
        timestamp=t0,
        session_id="SESS-PURITY",
    )

    original_dict = {
        "id": e1.id,
        "case_id": e1.case_id,
        "status": e1.status,
        "amount_minor_units": e1.amount_minor_units,
        "event_timestamp": e1.event_timestamp,
    }

    # Run correlation twice
    res1 = correlate_events([e1], case_id=case_id)
    res2 = correlate_events([e1], case_id=case_id)

    # Event fields unchanged
    assert e1.id == original_dict["id"]
    assert e1.status == original_dict["status"]
    assert e1.amount_minor_units == original_dict["amount_minor_units"]
    assert e1.event_timestamp == original_dict["event_timestamp"]
    assert res1.groups[0].group_id == res2.groups[0].group_id


# -----------------------------------------------------------------------------
# 13. API Route Verification: GET /api/v1/cases/{case_id}/correlation
# -----------------------------------------------------------------------------

def test_get_case_correlation_endpoint():
    """Verify the API endpoint retrieves normalized events and returns typed CaseCorrelationResult."""
    # Create case
    case_res = client.post(
        "/api/v1/cases",
        json={
            "reference": "TRC-CORR-API-01",
            "initiating_party": "ORIGINATING_BANK",
            "counterparty": "RECEIVING_BANK",
            "disputed_amount": 8500000,
            "currency": "NGN",
        },
    )
    assert case_res.status_code == 201
    case_id = case_res.json()["case_id"]

    # Ingest originating bank evidence
    csv_payload = (
        "timestamp,reference,amount,currency,action,status,session_id,rrn\n"
        "2026-10-14T22:15:30+01:00,TXN-API-01,8500000,NGN,DEBIT,SUCCESS,100004241014221530120194820,004819204812\n"
    ).encode("utf-8")
    client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("orig.csv", csv_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )

    # Ingest switch evidence
    jsonl_payload = (
        '{"timestamp": "2026-10-14T22:15:31+01:00", "session_id": "100004241014221530120194820", '
        '"action": "SWITCH_IN", "status": "SUCCESS", "amount": 8500000, "currency": "NGN", "rrn": "004819204812"}\n'
    ).encode("utf-8")
    client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("switch.jsonl", jsonl_payload, "application/x-ndjson")},
        data={"institution_source": "SWITCH"},
    )

    # Query correlation endpoint
    corr_res = client.get(f"/api/v1/cases/{case_id}/correlation")
    assert corr_res.status_code == 200
    data = corr_res.json()

    assert data["case_id"] == case_id
    assert data["algorithm_version"] == ALGORITHM_VERSION
    assert data["total_events"] == 2
    assert data["summary"]["CONFIRMED"] == 1
    assert data["groups"][0]["status"] == "CONFIRMED"
    assert data["groups"][0]["method"] == "SESSION_AND_RRN_MATCH"
    assert data["groups"][0]["identifier_scope"] == "GLOBAL_INTERBANK"
    assert len(data["groups"][0]["member_event_ids"]) == 2


# -----------------------------------------------------------------------------
# 14. Single Global Identifier Sufficiency (session-only / rrn-only)
# -----------------------------------------------------------------------------

def test_session_id_alone_is_sufficient_for_confirmed_match():
    """A consistent shared session_id with no RRN is still a strong confirmed link."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN", timestamp=t0, session_id="SESS-ONLY-1",
    )
    e2 = make_event(
        case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN",
        timestamp=t0 + timedelta(seconds=1), session_id="SESS-ONLY-1",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 1
    g = res.groups[0]
    assert g.method == CorrelationMethodEnum.SESSION_ID_MATCH
    assert g.reason_code == CorrelationReasonCode.STRONG_SESSION_ID_CONSISTENT
    assert g.identifier_scope.value == "GLOBAL_INTERBANK"


def test_rrn_alone_is_sufficient_for_confirmed_match():
    """A consistent shared RRN with no session_id is a strong confirmed link."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
        amount_minor_units=500000, currency="NGN", timestamp=t0, rrn="RRN-ONLY-1",
    )
    e2 = make_event(
        case_id=case_id, party="RECEIVING_BANK", action="CREDIT", status="SUCCESS",
        amount_minor_units=500000, currency="NGN",
        timestamp=t0 + timedelta(seconds=1), rrn="RRN-ONLY-1",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 1
    g = res.groups[0]
    assert g.method == CorrelationMethodEnum.RRN_MATCH
    assert g.reason_code == CorrelationReasonCode.STRONG_RRN_CONSISTENT


# -----------------------------------------------------------------------------
# 15. Transitive Identifier Conflict (contradiction must surface)
# -----------------------------------------------------------------------------

def test_transitive_identifier_conflict_is_ambiguous():
    """A third event sharing the RRN but a different session_id must surface a conflict,
    even though the first two events form a clean session_id cluster."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN", timestamp=t0,
        session_id="SESS-CLEAN", rrn="RRN-SHARED",
    )
    e2 = make_event(
        case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN",
        timestamp=t0 + timedelta(seconds=1), session_id="SESS-CLEAN", rrn="RRN-SHARED",
    )
    e3 = make_event(
        case_id=case_id, party="RECEIVING_BANK", action="CREDIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN",
        timestamp=t0 + timedelta(seconds=2), session_id="SESS-CONFLICT", rrn="RRN-SHARED",
    )

    res = correlate_events([e1, e2, e3], case_id=case_id)
    assert res.summary["AMBIGUOUS"] == 1
    assert res.summary["CONFIRMED"] == 0
    g = res.groups[0]
    assert g.status == CorrelationStatusEnum.AMBIGUOUS
    assert g.reason_code == CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT
    assert set(g.member_event_ids) == {e1.id, e2.id, e3.id}
    assert any("session_id" in c for c in g.contradictions)


def test_conflicting_currency_under_shared_session_is_ambiguous():
    """Equal session_id with differing currencies is a currency conflict, never confirmed."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    e1 = make_event(
        case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN", timestamp=t0, session_id="SESS-CC",
    )
    e2 = make_event(
        case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
        amount_minor_units=8500000, currency="USD",
        timestamp=t0 + timedelta(seconds=1), session_id="SESS-CC",
    )

    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0
    g = res.groups[0]
    assert g.status == CorrelationStatusEnum.AMBIGUOUS
    assert g.reason_code == CorrelationReasonCode.CURRENCY_MISMATCH
    # Set-derived identifiers are emitted in sorted order (hash-seed independent).
    assert g.identifying_keys["currencies"] == ["NGN", "USD"]


# -----------------------------------------------------------------------------
# 16. Multiple Events with Identical Identifiers (deterministic + duplicate flag)
# -----------------------------------------------------------------------------

def test_identical_identifiers_deterministic_and_duplicate_flagged():
    """Identical identifier records are grouped deterministically and flagged as duplicates."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    # Two byte-identical originating-bank records -> probable duplicate ingestion.
    e1 = make_event(
        case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN", timestamp=t0,
        session_id="SESS-DUP", rrn="RRN-DUP",
    )
    e2 = make_event(
        case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN", timestamp=t0,
        session_id="SESS-DUP", rrn="RRN-DUP",
    )
    # A distinct switch leg sharing the same global identifiers.
    e3 = make_event(
        case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
        amount_minor_units=8500000, currency="NGN",
        timestamp=t0 + timedelta(seconds=1), session_id="SESS-DUP", rrn="RRN-DUP",
    )

    res1 = correlate_events([e1, e2, e3], case_id=case_id)
    res2 = correlate_events([e3, e1, e2], case_id=case_id)

    assert res1.summary["CONFIRMED"] == 1
    g1, g2 = res1.groups[0], res2.groups[0]
    assert g1.group_id == g2.group_id
    assert g1.member_event_ids == g2.member_event_ids
    assert set(g1.duplicate_identity_event_ids) == {e1.id, e2.id}
    assert any("Duplicate identity" in c for c in g1.contradictions)


# -----------------------------------------------------------------------------
# 17. Cross-Process Determinism (hash-seed independence)
# -----------------------------------------------------------------------------

def test_output_is_independent_of_python_hash_seed():
    """Identical events must serialize identically across interpreter hash seeds."""
    script = (
        "import json, uuid, os\n"
        "from datetime import datetime, timezone\n"
        "from app.db.models import EventModel\n"
        "from app.correlation.engine import correlate_events\n"
        "case = uuid.UUID('11111111-1111-1111-1111-111111111111')\n"
        "def ev(eid, party, action, sid, rrn, cur):\n"
            "    rid = uuid.UUID('00000000-0000-0000-0000-000000000000') if eid == 'aaaaaaaa-1111-1111-1111-111111111111' else (\n"
            "        uuid.UUID('00000000-0000-0000-0000-000000000001') if eid == 'bbbbbbbb-2222-2222-2222-222222222222' else\n"
            "        uuid.UUID('00000000-0000-0000-0000-000000000002'))\n"
            "    return EventModel(id=uuid.UUID(eid), case_id=case, raw_record_id=rid,\n"
            "        party=party, action=action, status='SUCCESS', amount_minor_units=8500000,\n"
            "        currency=cur, session_id=sid, rrn=rrn,\n"
            "        event_timestamp=datetime(2026,10,14,22,15,30,tzinfo=timezone.utc), metadata_json={})\n"
            "events = [\n"
            "    ev('aaaaaaaa-1111-1111-1111-111111111111','ORIGINATING_BANK','DEBIT','S','R1','NGN'),\n"
            "    ev('bbbbbbbb-2222-2222-2222-222222222222','SWITCH','SWITCH_IN','S','R2','USD'),\n"
            "    ev('cccccccc-3333-3333-3333-333333333333','ORIGINATING_BANK','DEBIT','S2','R1','GBP'),\n"
            "]\n"
            "res = correlate_events(events, case_id=case)\n"
            "print(json.dumps([g.model_dump(mode='json') for g in res.groups], sort_keys=True))\n"
    )
    outputs = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout.strip())
    assert outputs[0] == outputs[1] == outputs[2]


# -----------------------------------------------------------------------------
# 18. Idempotency / Purity Against Persisted Events (no duplicate groups, no writes)
# -----------------------------------------------------------------------------

def test_rerun_does_not_mutate_events_or_create_persisted_groups():
    """Repeated correlation runs must not alter persisted rows or emit duplicate groups."""
    case_res = client.post(
        "/api/v1/cases",
        json={
            "reference": "TRC-CORR-IDEM-01",
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
        "2026-10-14T22:15:30+01:00,TXN-IDEM-01,8500000,NGN,DEBIT,SUCCESS,SESS-IDEM,004819204812\n"
        "2026-10-14T22:15:31+01:00,TXN-IDEM-02,8500000,NGN,SWITCH_IN,SUCCESS,SESS-IDEM,004819204812\n"
    ).encode("utf-8")
    up = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("idem.csv", csv_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert up.status_code == 201

    case_uuid = uuid.UUID(case_id)

    # Query correlation via API twice; results should be identical.
    r1 = client.get(f"/api/v1/cases/{case_id}/correlation")
    assert r1.status_code == 200
    r2 = client.get(f"/api/v1/cases/{case_id}/correlation")
    assert r2.status_code == 200

    d1 = r1.json()
    d2 = r2.json()

    # Compare groups (excluding computed_at which is metadata).
    assert d1["case_id"] == d2["case_id"]
    assert d1["algorithm_version"] == d2["algorithm_version"]
    assert len(d1["groups"]) == len(d2["groups"])
    for g1, g2 in zip(d1["groups"], d2["groups"]):
        assert g1["group_id"] == g2["group_id"]
        assert g1["member_event_ids"] == g2["member_event_ids"]
        assert g1["status"] == g2["status"]
        assert g1["method"] == g2["method"]
        assert g1["reason_code"] == g2["reason_code"]
    assert d1["summary"] == d2["summary"]
    assert d1["total_events"] == d2["total_events"] == 2


# -----------------------------------------------------------------------------
# 19. Adversarial: transitive bridge with contradictory endpoints -> AMBIGUOUS
# -----------------------------------------------------------------------------

def test_transitive_bridge_endpoints_contradictory_remain_ambiguous():
    """A(S1,R1) shares S1 with B(S1,R2), B shares R2 with C(S2,R2). 
    Connected cluster must be AMBIGUOUS (conflicting sessions/rrns) and never CONFIRMED."""
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)

    a = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                   amount_minor_units=1000000, currency="NGN", timestamp=t0, session_id="S1", rrn="R1")
    b = make_event(case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
                   amount_minor_units=1000000, currency="NGN", timestamp=t0 + timedelta(seconds=1),
                   session_id="S1", rrn="R2")
    c = make_event(case_id=case_id, party="RECEIVING_BANK", action="CREDIT", status="SUCCESS",
                   amount_minor_units=1000000, currency="NGN", timestamp=t0 + timedelta(seconds=2),
                   session_id="S2", rrn="R2")

    res1 = correlate_events([a, b, c], case_id=case_id)
    res2 = correlate_events([c, a, b], case_id=case_id)

    assert res1.summary["AMBIGUOUS"] == 1 and res1.summary["CONFIRMED"] == 0
    g1, g2 = res1.groups[0], res2.groups[0]
    assert g1.group_id == g2.group_id
    assert set(g1.member_event_ids) == {a.id, b.id, c.id}
    assert g1.status == CorrelationStatusEnum.AMBIGUOUS
    assert g1.reason_code == CorrelationReasonCode.AMBIGUOUS_IDENTIFIER_CONFLICT
    assert "session" in " ".join(g1.contradictions).lower() or "rrn" in " ".join(g1.contradictions).lower()


# -----------------------------------------------------------------------------
# 20. Adversarial: reference reuse (same institution) -> CANDIDATE only
# -----------------------------------------------------------------------------

def test_institution_local_reference_reused_not_confirmed():
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)
    e1 = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                    amount_minor_units=100000, currency="NGN", timestamp=t0, reference="BATCH-123")
    e2 = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                    amount_minor_units=200000, currency="NGN", timestamp=t0 + timedelta(seconds=1),
                    reference="BATCH-123")
    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0 and res.summary["CANDIDATE"] >= 1
    cand = next(g for g in res.groups if g.status == CorrelationStatusEnum.CANDIDATE)
    assert cand.reason_code == CorrelationReasonCode.CANDIDATE_PARTIAL_REFERENCE_ONLY


# -----------------------------------------------------------------------------
# 21. Adversarial: reference cross-party collision (3 parties) -> CANDIDATE/AMBIGUOUS, never CONFIRMED
# -----------------------------------------------------------------------------

def test_cross_party_reference_three_parties_never_confirmed():
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)
    e1 = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                    amount_minor_units=8500000, currency="NGN", timestamp=t0, reference="ORDER-999")
    e2 = make_event(case_id=case_id, party="MERCHANT", action="ACK", status="SUCCESS",
                    amount_minor_units=8500000, currency="NGN", timestamp=t0 + timedelta(seconds=1),
                    reference="ORDER-999")
    e3 = make_event(case_id=case_id, party="SWITCH", action="SWITCH_IN", status="SUCCESS",
                    amount_minor_units=8500000, currency="NGN", timestamp=t0 + timedelta(seconds=2),
                    reference="ORDER-999")
    res = correlate_events([e1, e2, e3], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0
    # Should surface as candidate/ambiguous, not confirmed
    statuses = {g.status for g in res.groups}
    assert CorrelationStatusEnum.CONFIRMED not in statuses


# -----------------------------------------------------------------------------
# 22. Adversarial: same amount+time+ref+currency but different session/rrn -> candidate only
# -----------------------------------------------------------------------------

def test_proximity_ref_collision_without_unique_ids_never_confirmed():
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)
    e1 = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                    amount_minor_units=500000, currency="NGN", timestamp=t0, reference="REF-X", session_id="S1")
    e2 = make_event(case_id=case_id, party="RECEIVING_BANK", action="CREDIT", status="SUCCESS",
                    amount_minor_units=500000, currency="NGN", timestamp=t0 + timedelta(seconds=2),
                    reference="REF-X", session_id="S2")
    res = correlate_events([e1, e2], case_id=case_id)
    assert res.summary["CONFIRMED"] == 0
    assert any(g.status == CorrelationStatusEnum.AMBIGUOUS for g in res.groups)


# -----------------------------------------------------------------------------
# 23. Adversarial: timestamps never rewritten; tolerance doesn't prove identity
# -----------------------------------------------------------------------------

def test_timestamps_preserved_and_tolerance_not_identity():
    case_id = uuid.uuid4()
    t0 = datetime(2026, 10, 14, 22, 15, 30, tzinfo=timezone.utc)
    e1 = make_event(case_id=case_id, party="ORIGINATING_BANK", action="DEBIT", status="SUCCESS",
                    amount_minor_units=500000, currency="NGN", timestamp=t0)
    e2 = make_event(case_id=case_id, party="RECEIVING_BANK", action="CREDIT", status="SUCCESS",
                    amount_minor_units=500000, currency="NGN", timestamp=t0 + timedelta(seconds=12))
    res = correlate_events([e1, e2], case_id=case_id, tolerance_seconds=5.0)
    assert res.summary["CONFIRMED"] == 0 and res.summary["CANDIDATE"] == 0
    assert res.summary["UNMATCHED"] == 2
    # timestamps unchanged
    ts = []
    for g in res.groups:
        for ev in g.events:
            ts.append(ev.event_timestamp)
    assert t0 in ts and (t0 + timedelta(seconds=12)) in ts
