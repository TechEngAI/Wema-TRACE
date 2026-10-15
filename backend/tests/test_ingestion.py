"""Comprehensive Ingestion and Normalization Test Suite (Part 2).

Tests:
1. Valid CSV ingestion across institutions
2. Valid JSONL ingestion across institutions
3. Empty file handling
4. Malformed JSONL line detection
5. Invalid CSV headers
6. Missing required fields
7. Invalid amount representations (floats and negative values)
8. Unsupported currencies
9. Timezone-naive timestamp rejection (no silent UTC inference)
10. Malformed timestamp rejection
11. Oversized file rejection (HTTP 413)
12. Duplicate evidence detection (HTTP 409)
13. Unknown case handling (HTTP 404)
14. Transactional atomicity (no partial records on failure)
15. Lineage and unmutated raw records
16. Strict absence of cross-source correlation / rules logic
"""

from pathlib import Path
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import CaseModel, EventModel, RawRecordModel, SourceFileModel
from app.db.session import get_db
from app.main import app

from sqlalchemy.pool import StaticPool

# Test database isolation: use an in-memory SQLite database for testing with StaticPool
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

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure clean database state before each test run."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


def create_test_case(ref: str = "TRC-TEST-001") -> str:
    """Helper to create a dispute case for testing."""
    res = client.post(
        "/api/v1/cases",
        json={
            "reference": ref,
            "initiating_party": "ORIGINATING_BANK",
            "counterparty": "RECEIVING_BANK",
            "disputed_amount": 8500000,
            "currency": "NGN",
        },
    )
    assert res.status_code == 201, f"Failed to create test case: {res.text}"
    return res.json()["case_id"]


def test_valid_csv_ingestion():
    """Verify valid originating bank CSV is ingested, preserved, and normalized."""
    case_id = create_test_case("TRC-CSV-VALID")
    file_path = FIXTURES_DIR / "originating_bank_valid.csv"

    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("originating_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )

    assert res.status_code == 201
    data = res.json()
    assert data["case_id"] == case_id
    assert data["records_ingested"] == 2
    assert data["status"] == "INGESTED"
    assert "sha256_hash" in data
    source_file_id = data["source_file_id"]

    # Verify DB persistence directly
    db = TestingSessionLocal()
    sf = db.query(SourceFileModel).filter_by(id=uuid.UUID(source_file_id)).first()
    assert sf is not None
    assert sf.record_count == 2
    assert sf.file_type == "CSV"

    raw_records = db.query(RawRecordModel).filter_by(source_file_id=sf.id).order_by(RawRecordModel.record_index).all()
    assert len(raw_records) == 2
    assert raw_records[0].raw_payload["reference"] == "TXN-ORIG-902183"

    events = db.query(EventModel).filter_by(case_id=uuid.UUID(case_id)).all()
    assert len(events) == 2
    assert events[0].amount_minor_units == 8500000
    assert events[0].party == "ORIGINATING_BANK"
    assert events[0].action == "DEBIT"
    assert events[0].status == "SUCCESS"
    assert events[0].raw_record_id == raw_records[0].id
    assert events[0].event_timestamp.tzinfo is not None
    db.close()


def test_valid_jsonl_ingestion():
    """Verify valid switch JSONL is ingested, preserved, and normalized."""
    case_id = create_test_case("TRC-JSONL-VALID")
    file_path = FIXTURES_DIR / "switch_valid.jsonl"

    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("switch_valid.jsonl", f, "application/x-ndjson")},
            data={"institution_source": "SWITCH"},
        )

    assert res.status_code == 201
    data = res.json()
    assert data["records_ingested"] == 2

    db = TestingSessionLocal()
    events = db.query(EventModel).filter_by(case_id=uuid.UUID(case_id)).order_by(EventModel.action).all()
    assert len(events) == 2
    actions = {e.action for e in events}
    assert actions == {"SWITCH_IN", "SWITCH_OUT"}
    # Verify timeout status was preserved exactly, not altered
    timeout_event = [e for e in events if e.action == "SWITCH_OUT"][0]
    assert timeout_event.status == "TIMEOUT"
    db.close()


def test_receiving_bank_and_merchant_ingestion():
    """Verify receiving bank and merchant categories ingest successfully."""
    case_id = create_test_case("TRC-MULTI-INGEST")

    # Ingest Receiving Bank
    rcv_file = FIXTURES_DIR / "receiving_bank_valid.csv"
    with open(rcv_file, "rb") as f:
        res_rcv = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("receiving_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "RECEIVING_BANK"},
        )
    assert res_rcv.status_code == 201
    assert res_rcv.json()["records_ingested"] == 1

    # Ingest Merchant
    merch_file = FIXTURES_DIR / "merchant_valid.jsonl"
    with open(merch_file, "rb") as f:
        res_m = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("merchant_valid.jsonl", f, "application/x-ndjson")},
            data={"institution_source": "MERCHANT"},
        )
    assert res_m.status_code == 201
    assert res_m.json()["records_ingested"] == 1

    # Verify case details endpoint shows 2 total events
    case_res = client.get(f"/api/v1/cases/{case_id}")
    assert case_res.status_code == 200
    assert case_res.json()["events_count"] == 2


def test_empty_file_rejected():
    """Verify empty evidence file is rejected with 400 Bad Request."""
    case_id = create_test_case("TRC-EMPTY")
    res = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("empty.csv", b"", "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert res.status_code == 400
    assert "empty" in res.json()["error"].lower()


def test_malformed_jsonl_line_rejected():
    """Verify malformed JSON on a specific line is rejected with line detail."""
    case_id = create_test_case("TRC-BAD-JSONL")
    file_path = FIXTURES_DIR / "malformed_line.jsonl"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("malformed_line.jsonl", f, "application/x-ndjson")},
            data={"institution_source": "SWITCH"},
        )
    assert res.status_code == 400
    assert "line 2" in res.json()["error"].lower()


def test_invalid_csv_headers_rejected():
    """Verify CSV with invalid headers is rejected without guessing column meanings."""
    case_id = create_test_case("TRC-BAD-HDRS")
    file_path = FIXTURES_DIR / "invalid_headers.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("invalid_headers.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "missing required field" in res.json()["error"].lower()


def test_missing_required_fields_rejected():
    """Verify records missing required fields are rejected."""
    case_id = create_test_case("TRC-MISSING-FIELDS")
    file_path = FIXTURES_DIR / "missing_fields.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("missing_fields.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "reference" in res.json()["error"].lower()


def test_invalid_amount_float_rejected():
    """Verify floating-point monetary amounts are strictly prohibited."""
    case_id = create_test_case("TRC-FLOAT-AMT")
    file_path = FIXTURES_DIR / "invalid_amount_float.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("invalid_amount_float.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "floating-point" in res.json()["error"].lower()


def test_invalid_amount_negative_rejected():
    """Verify negative monetary amounts are rejected."""
    case_id = create_test_case("TRC-NEG-AMT")
    file_path = FIXTURES_DIR / "invalid_amount_negative.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("invalid_amount_negative.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "positive" in res.json()["error"].lower()


def test_unsupported_currency_rejected():
    """Verify unsupported currency codes are rejected."""
    case_id = create_test_case("TRC-BAD-CURR")
    file_path = FIXTURES_DIR / "unsupported_currency.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("unsupported_currency.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "unsupported currency" in res.json()["error"].lower()


def test_timezone_naive_timestamp_rejected():
    """Verify timezone-naive timestamps are rejected (no silent UTC inference)."""
    case_id = create_test_case("TRC-NAIVE-TIME")
    file_path = FIXTURES_DIR / "timezone_naive.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("timezone_naive.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "timezone offset missing" in res.json()["error"].lower()


def test_malformed_timestamp_rejected():
    """Verify malformed timestamp format is rejected."""
    case_id = create_test_case("TRC-MALFORMED-TIME")
    file_path = FIXTURES_DIR / "malformed_timestamp.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("malformed_timestamp.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 400
    assert "invalid iso 8601 timestamp" in res.json()["error"].lower()


def test_oversized_upload_rejected(monkeypatch):
    """Verify file exceeding byte ceiling is rejected with HTTP 413."""
    case_id = create_test_case("TRC-OVERSIZED")
    # Temporarily set max size to 100 bytes for test
    from app.config import settings
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 100)

    large_payload = b"timestamp,reference,amount,currency,status\n" + b"x" * 200
    res = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("large.csv", large_payload, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert res.status_code == 413
    assert "exceeds maximum allowable limit" in res.json()["error"].lower()


def test_duplicate_upload_rejected():
    """Verify identical file upload to same case is rejected with HTTP 409 Conflict."""
    case_id = create_test_case("TRC-DUP-CHECK")
    file_path = FIXTURES_DIR / "originating_bank_valid.csv"

    # First upload -> Success
    with open(file_path, "rb") as f:
        res1 = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("originating_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res1.status_code == 201

    # Second upload with same content -> 409 Conflict
    with open(file_path, "rb") as f:
        res2 = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("originating_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res2.status_code == 409
    assert "duplicate evidence detected" in res2.json()["error"].lower()


def test_unknown_case_rejected():
    """Verify upload targeting a non-existent case ID returns HTTP 404."""
    unknown_id = uuid.uuid4()
    file_path = FIXTURES_DIR / "originating_bank_valid.csv"
    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{unknown_id}/evidence",
            files={"file": ("originating_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 404
    assert "not found" in res.json()["error"].lower()


def test_atomic_rollback_on_failed_import():
    """Verify that a batch with a valid first row and an invalid second row rolls back completely."""
    case_id = create_test_case("TRC-ATOMIC-TEST")

    # CSV with row 1 valid and row 2 containing a float
    mixed_csv = (
        "timestamp,reference,amount,currency,status\n"
        "2026-10-14T22:15:30+01:00,TXN-GOOD,100000,NGN,SUCCESS\n"
        "2026-10-14T22:15:35+01:00,TXN-BAD,1000.50,NGN,SUCCESS\n"
    ).encode("utf-8")

    res = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        files={"file": ("mixed.csv", mixed_csv, "text/csv")},
        data={"institution_source": "ORIGINATING_BANK"},
    )
    assert res.status_code == 400

    # Ensure zero records remain in database for this case
    db = TestingSessionLocal()
    files_count = db.query(SourceFileModel).filter_by(case_id=uuid.UUID(case_id)).count()
    raw_count = db.query(RawRecordModel).filter_by(case_id=uuid.UUID(case_id)).count()
    events_count = db.query(EventModel).filter_by(case_id=uuid.UUID(case_id)).count()
    db.close()

    assert files_count == 0, "Source files record was not rolled back!"
    assert raw_count == 0, "Raw records were partially saved!"
    assert events_count == 0, "Events were partially saved!"


def test_no_correlation_or_outcome_determination_in_part_2():
    """Verify that ingestion strictly does NOT produce analysis, correlation, or outcome decisions."""
    case_id = create_test_case("TRC-NO-CORRELATION")
    file_path = FIXTURES_DIR / "originating_bank_valid.csv"

    with open(file_path, "rb") as f:
        res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            files={"file": ("originating_bank_valid.csv", f, "text/csv")},
            data={"institution_source": "ORIGINATING_BANK"},
        )
    assert res.status_code == 201

    # Check case endpoint has no persisted latest_analysis (analysis is compute-on-demand)
    case_res = client.get(f"/api/v1/cases/{case_id}")
    assert case_res.status_code == 200
    assert case_res.json()["latest_analysis"] is None

    # Analyze endpoint is implemented in Part 4 and returns a deterministic contract.
    analyze_res = client.post(f"/api/v1/cases/{case_id}/analyze")
    assert analyze_res.status_code == 200
    analysis = analyze_res.json()
    assert analysis["case_id"] == case_id
    assert analysis["outcome"] in {
        "DISCREPANCY_CONFIRMED",
        "LIKELY_ROOT_CAUSE",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "NO_DISCREPANCY_FOUND",
    }
    # Ingestion itself never persists an analysis artifact.
    assert case_res.json()["latest_analysis"] is None
