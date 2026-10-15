"""Test domain contracts, enums, and shared analysis schema validation."""

from datetime import datetime, timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError

from app.schemas.analysis import (
    AnalysisResponse,
    CounterfactualItem,
    DataQualityFlagItem,
    MissingEvidenceItem,
    TimelineEvent,
)
from app.schemas.case import CaseCreateRequest
from app.schemas.enums import (
    EventActionEnum,
    EventStatusEnum,
    FlagSeverityEnum,
    InstitutionPartyEnum,
    OutcomeEnum,
    StrengthEnum,
)


def test_outcome_enums_exact_set():
    """Verify that OutcomeEnum contains EXACTLY the five permitted values."""
    expected_outcomes = {
        "DISCREPANCY_CONFIRMED",
        "LIKELY_ROOT_CAUSE",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "NO_DISCREPANCY_FOUND",
    }
    actual_outcomes = {e.value for e in OutcomeEnum}
    assert actual_outcomes == expected_outcomes
    assert len(OutcomeEnum) == 5


def test_strength_labels_exact_set():
    """Verify that StrengthEnum contains EXACTLY the four permitted qualitative labels."""
    expected_strengths = {
        "HIGH",
        "MODERATE",
        "LOW",
        "INSUFFICIENT",
    }
    actual_strengths = {s.value for s in StrengthEnum}
    assert actual_strengths == expected_strengths
    assert len(StrengthEnum) == 4


def test_analysis_response_contract_valid():
    """Verify that AnalysisResponse serializes and validates all required fields."""
    case_id = uuid4()
    event_id = uuid4()
    raw_record_id = uuid4()
    now = datetime.now(timezone.utc)

    analysis = AnalysisResponse(
        case_id=case_id,
        outcome=OutcomeEnum.DISCREPANCY_CONFIRMED,
        strength=StrengthEnum.HIGH,
        rule_version="2026.1.0",
        category="SETTLEMENT_DELAY_UNREVERSED_DEBIT",
        timeline=[
            TimelineEvent(
                event_id=event_id,
                timestamp=now,
                party=InstitutionPartyEnum.ORIGINATING_BANK,
                action=EventActionEnum.DEBIT,
                status=EventStatusEnum.SUCCESS,
                amount_minor_units=8500000,
                currency="NGN",
                reference="REF-001",
                session_id="SESS-12345",
                rrn="RRN-998877",
                channel="MOBILE",
                raw_record_id=raw_record_id,
            )
        ],
        supporting_event_ids=[event_id],
        missing_evidence=[
            MissingEvidenceItem(
                expected_party=InstitutionPartyEnum.RECEIVING_BANK,
                expected_action=EventActionEnum.CREDIT,
                window_start=now,
                window_end=now,
                reason="No credit record found in receiving bank journal",
            )
        ],
        data_quality_flags=[
            DataQualityFlagItem(
                flag_code="MINOR_CLOCK_DRIFT",
                severity=FlagSeverityEnum.LOW,
                description="Clock drift 1.2s",
                affected_event_ids=[event_id],
                impact="None",
            )
        ],
        counterfactuals=[
            CounterfactualItem(
                scenario="Late credit arrival",
                condition="Credit record received with valid session ID",
                outcome_if_condition_met=OutcomeEnum.NO_DISCREPANCY_FOUND,
                explanation="Late delivery rather than failed transaction",
            )
        ],
    )

    data = analysis.model_dump()
    # Ensure all 10 required contract fields are present
    assert "case_id" in data
    assert "outcome" in data
    assert "strength" in data
    assert "rule_version" in data
    assert "category" in data
    assert "timeline" in data
    assert "supporting_event_ids" in data
    assert "missing_evidence" in data
    assert "data_quality_flags" in data
    assert "counterfactuals" in data

    assert data["outcome"] == "DISCREPANCY_CONFIRMED"
    assert data["strength"] == "HIGH"
    assert data["timeline"][0]["amount_minor_units"] == 8500000


def test_analysis_response_rejects_invalid_outcome():
    """Verify that an arbitrary or AI-invented outcome raises ValidationError."""
    with pytest.raises(ValidationError):
        AnalysisResponse(
            case_id=uuid4(),
            outcome="PROBABLE_FRAUD",  # Invalid outcome!
            strength=StrengthEnum.HIGH,
            rule_version="2026.1.0",
            category="FRAUD",
        )


def test_analysis_response_rejects_numeric_strength():
    """Verify that numeric percentages or arbitrary strength labels raise ValidationError."""
    with pytest.raises(ValidationError):
        AnalysisResponse(
            case_id=uuid4(),
            outcome=OutcomeEnum.DISCREPANCY_CONFIRMED,
            strength="95%",  # Prohibited percentage!
            rule_version="2026.1.0",
            category="TEST",
        )


def test_monetary_minor_units_enforcement():
    """Verify that negative or zero disputed amounts are rejected in CaseCreateRequest."""
    with pytest.raises(ValidationError):
        CaseCreateRequest(
            reference="TRC-INVALID",
            initiating_party=InstitutionPartyEnum.ORIGINATING_BANK,
            counterparty=InstitutionPartyEnum.RECEIVING_BANK,
            disputed_amount=0,  # Invalid: gt=0 required
        )
