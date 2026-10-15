"""Health endpoint, route registration, and endpoint status tests."""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db

client = TestClient(app)


def test_root_health():
    """Verify that the root health endpoint returns 200 OK and valid payload."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "trace-backend"
    assert "version" in data
    assert "timestamp" in data


def test_v1_health():
    """Verify that the /api/v1/health endpoint returns 200 OK."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "trace-backend"
    assert data["version"] == "0.1.0"


def test_openapi_schema_generation():
    """Verify OpenAPI schema generation contains all planned routes."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})

    expected_routes = [
        "/health",
        "/api/v1/health",
        "/api/v1/cases",
        "/api/v1/cases/{case_id}",
        "/api/v1/cases/{case_id}/evidence",
        "/api/v1/cases/{case_id}/correlation",
        "/api/v1/cases/{case_id}/analyze",
        "/api/v1/cases/{case_id}/evidence-pack",
        "/api/v1/cases/{case_id}/complaint-email",
        "/api/v1/cases/{case_id}/review",
        "/api/v1/cases/{case_id}/reviews",
    ]

    for route in expected_routes:
        assert route in paths, f"Expected route {route} not found in OpenAPI paths"


def test_implemented_business_endpoint_statuses():
    """Verify implemented endpoints return 404 for unknown cases."""
    dummy_case_id = uuid.uuid4()

    class EmptyCaseQuery:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    class EmptyDb:
        def query(self, *args, **kwargs):
            return EmptyCaseQuery()

    def override_get_db():
        yield EmptyDb()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    try:
        # Analyze endpoint is implemented in Part 4; unknown case yields 404 (not 501).
        res_analyze = client.post(f"/api/v1/cases/{dummy_case_id}/analyze")
        assert res_analyze.status_code == 404

        # Evidence-pack export is implemented in Part 6; unknown case also yields 404.
        res_pack = client.get(f"/api/v1/cases/{dummy_case_id}/evidence-pack")
        assert res_pack.status_code == 404

        # Complaint-email drafting is implemented in Part 7; unknown case also yields 404.
        res_email = client.post(
            f"/api/v1/cases/{dummy_case_id}/complaint-email",
            json={"recipient_institution": "SWITCH"},
        )
        assert res_email.status_code == 404
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    # Human review endpoint (Part 8) validates its current request contract before lookup.
    dummy_review = {
        "reviewer_display_name": "Synthetic reviewer",
        "decision": "ACCEPT_FINDING",
        "rationale": "The deterministic finding was reviewed.",
    }
    res_review = client.post(f"/api/v1/cases/{dummy_case_id}/review", json=dummy_review)
    assert res_review.status_code == 404
    assert client.get(f"/api/v1/cases/{dummy_case_id}/reviews").status_code == 404
