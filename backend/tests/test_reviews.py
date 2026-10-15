"""Part 8 review API tests: human decisions remain separate from machine findings."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import CaseReviewModel
from app.db.session import get_db
from app.main import app


engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


def make_case(reference="TRC-REVIEW-01"):
    response = client.post("/api/v1/cases", json={
        "reference": reference,
        "initiating_party": "ORIGINATING_BANK",
        "counterparty": "RECEIVING_BANK",
        "disputed_amount": 1000,
        "currency": "NGN",
    })
    assert response.status_code == 201
    return response.json()["case_id"]


def review_payload(decision="ACCEPT_FINDING", **overrides):
    payload = {
        "reviewer_display_name": "Synthetic Investigator",
        "decision": decision,
        "rationale": "The submitted evidence and machine finding were reviewed.",
        "notes": "Synthetic MVP review only.",
        "requested_additional_evidence": ["Receiving-bank settlement journal"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("decision", ["ACCEPT_FINDING", "DISPUTE_FINDING", "REQUEST_MORE_EVIDENCE"])
def test_records_each_controlled_human_decision_without_changing_machine_finding(decision):
    case_id = make_case(f"TRC-REVIEW-{decision}")
    before = client.post(f"/api/v1/cases/{case_id}/analyze").json()
    response = client.post(f"/api/v1/cases/{case_id}/review", json=review_payload(decision))
    after = client.post(f"/api/v1/cases/{case_id}/analyze").json()
    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == decision
    assert body["reviewer_identity_verified"] is False
    assert body["machine_finding"]["outcome"] == before["outcome"] == after["outcome"]
    assert body["machine_finding"]["strength"] == before["strength"] == after["strength"]
    assert body["requested_additional_evidence"] == ["Receiving-bank settlement journal"]


def test_review_history_is_append_only_and_case_scoped():
    case_one = make_case("TRC-REVIEW-HISTORY-1")
    case_two = make_case("TRC-REVIEW-HISTORY-2")
    assert client.post(f"/api/v1/cases/{case_one}/review", json=review_payload()).status_code == 201
    assert client.post(f"/api/v1/cases/{case_one}/review", json=review_payload("DISPUTE_FINDING")).status_code == 201
    assert client.post(f"/api/v1/cases/{case_two}/review", json=review_payload()).status_code == 201
    history = client.get(f"/api/v1/cases/{case_one}/reviews")
    assert history.status_code == 200
    assert len(history.json()["reviews"]) == 2
    assert all(item["case_id"] == case_one for item in history.json()["reviews"])


def test_review_validation_and_unknown_case_handling():
    case_id = make_case("TRC-REVIEW-VALIDATION")
    assert client.post(f"/api/v1/cases/{uuid.uuid4()}/review", json=review_payload()).status_code == 404
    assert client.get(f"/api/v1/cases/{uuid.uuid4()}/reviews").status_code == 404
    assert client.post(f"/api/v1/cases/{case_id}/review", json=review_payload("NOT_A_DECISION")).status_code == 422
    assert client.post(f"/api/v1/cases/{case_id}/review", json=review_payload(rationale="   ")).status_code == 422
    assert client.post(f"/api/v1/cases/{case_id}/review", json=review_payload(notes="x" * 4001)).status_code == 422
    forbidden = review_payload()
    forbidden["outcome"] = "NO_DISCREPANCY_FOUND"
    forbidden["strength"] = "HIGH"
    response = client.post(f"/api/v1/cases/{case_id}/review", json=forbidden)
    assert response.status_code == 201
    assert "outcome" not in response.json()


def test_database_failure_rolls_back_review(monkeypatch):
    case_id = make_case("TRC-REVIEW-ROLLBACK")
    original_commit = Session.class_.commit

    def fail_commit(session):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Session.class_, "commit", fail_commit)
    response = client.post(f"/api/v1/cases/{case_id}/review", json=review_payload())
    monkeypatch.setattr(Session.class_, "commit", original_commit)
    assert response.status_code == 500
    db = Session()
    try:
        assert db.query(CaseReviewModel).count() == 0
    finally:
        db.close()
