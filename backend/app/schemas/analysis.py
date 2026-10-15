"""Shared Analysis Response and Sub-schema Contracts.

This contract defines the output of the deterministic rules engine.
Nested objects are explicitly typed without arbitrary unvalidated dictionaries.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field

from app.schemas.enums import (
    EventActionEnum,
    EventStatusEnum,
    FlagSeverityEnum,
    InstitutionPartyEnum,
    OutcomeEnum,
    StrengthEnum,
)


class TimelineEvent(BaseModel):
    """Canonical normalized timeline event linked to raw evidence."""

    event_id: UUID = Field(description="Unique identifier of the normalized event")
    timestamp: datetime = Field(description="Normalized ISO 8601 timestamp with timezone")
    party: InstitutionPartyEnum = Field(description="Institution producing or executing the leg")
    action: EventActionEnum = Field(description="Normalized action classification")
    status: EventStatusEnum = Field(description="Execution status of this leg")
    amount_minor_units: int = Field(
        description="Transaction amount in integer minor units (e.g. kobo, cents)"
    )
    currency: str = Field(default="NGN", description="3-character ISO currency code")
    reference: Optional[str] = Field(default=None, description="Institution transaction reference")
    session_id: Optional[str] = Field(default=None, description="Global inter-bank session identifier")
    rrn: Optional[str] = Field(default=None, description="Retrieval Reference Number")
    channel: Optional[str] = Field(default=None, description="Initiation channel (e.g., POS, USSD, MOBILE)")
    raw_record_id: UUID = Field(description="Immutable foreign key linkage to raw source record")
    data_quality_notes: Optional[str] = Field(default=None, description="Caveats or normalization notes")


class MissingEvidenceItem(BaseModel):
    """Expected evidence item that was absent from ingested records."""

    expected_party: InstitutionPartyEnum = Field(description="Institution expected to provide evidence")
    expected_action: EventActionEnum = Field(description="Action expected to have been recorded")
    window_start: datetime = Field(description="Start of the expected time window")
    window_end: datetime = Field(description="End of the expected time window")
    reason: str = Field(description="Explanation of why this record was expected")
    evidence_type: Optional[str] = Field(
        default=None, description="High-level category of the missing evidence"
    )
    description: Optional[str] = Field(
        default=None, description="Human-readable description of the absent record"
    )
    impact: Optional[str] = Field(
        default=None, description="Deterministic impact of the absence on the finding"
    )
    recommended_request: Optional[str] = Field(
        default=None, description="Actionable request to the responsible institution"
    )


class RecommendedEvidenceItem(BaseModel):
    """Recommended follow-up evidence acquisition to strengthen resolution."""

    evidence_type: str = Field(description="Category of evidence requested")
    expected_party: Optional[InstitutionPartyEnum] = Field(
        default=None, description="Institution expected to supply the evidence"
    )
    rationale: str = Field(description="Why this evidence would materially change the finding")
    related_event_ids: List[UUID] = Field(
        default_factory=list,
        description="Existing event IDs the request relates to",
    )


class DataQualityFlagItem(BaseModel):
    """Data quality anomaly detected during evidence audit.

    CRITICAL severity flags mandate capping the finding strength at MODERATE.
    """

    flag_code: str = Field(description="Machine-readable error/warning code")
    severity: FlagSeverityEnum = Field(description="Severity classification")
    description: str = Field(description="Human-readable description of anomaly")
    affected_event_ids: List[UUID] = Field(
        default_factory=list,
        description="IDs of events directly impacted by this flag",
    )
    impact: str = Field(description="Deterministic impact on evaluation and strength")


class CounterfactualItem(BaseModel):
    """Alternative hypothesis or 'what-if' condition."""

    scenario: str = Field(description="Title or summary of hypothetical scenario")
    condition: str = Field(description="Specific condition that would alter the finding")
    outcome_if_condition_met: OutcomeEnum = Field(
        description="Resulting outcome if the condition were satisfied"
    )
    explanation: str = Field(description="Causal explanation of how the finding would change")


class AnalysisExplanation(BaseModel):
    """Optional evidence-grounded prose; never an authority for the analysis."""

    summary: str = Field(min_length=1, description="Plain-language summary, maximum 80 words")
    reasoning_explanation: str = Field(min_length=1)
    supporting_explanation: str = Field(min_length=1)
    contradiction_explanation: str = Field(min_length=1)
    uncertainty_explanation: str = Field(min_length=1)
    next_step_explanation: str = Field(min_length=1)


class AnalysisResponse(BaseModel):
    """Canonical deterministic analysis response contract."""

    case_id: UUID = Field(description="Target case identifier")
    outcome: OutcomeEnum = Field(
        description="Deterministic finding outcome (one of the 5 permitted enums)"
    )
    strength: StrengthEnum = Field(
        description="Qualitative strength label (one of the 4 permitted labels; NO percentages)"
    )
    rule_version: str = Field(description="Deterministic rules engine version (e.g., 2026.1.0)")
    category: str = Field(description="Domain categorization of the discrepancy pattern")
    timeline: List[TimelineEvent] = Field(
        default_factory=list,
        description="Chronological, correlation-verified canonical timeline",
    )
    supporting_event_ids: List[UUID] = Field(
        default_factory=list,
        description="Event IDs directly establishing the outcome",
    )
    missing_evidence: List[MissingEvidenceItem] = Field(
        default_factory=list,
        description="Expected records absent from the evidence submission",
    )
    data_quality_flags: List[DataQualityFlagItem] = Field(
        default_factory=list,
        description="Data anomalies identified during ingestion or audit",
    )
    counterfactuals: List[CounterfactualItem] = Field(
        default_factory=list,
        description="Counterfactual analysis scenarios and sensitivity bounds",
    )
    outcome_reason: str = Field(
        default="",
        description="Deterministic, non-probabilistic justification for the selected outcome",
    )
    strength_reason: str = Field(
        default="",
        description="Deterministic justification for the selected qualitative strength label",
    )
    scope_examined: str = Field(
        default="",
        description="Summary of the evidence scope actually evaluated by the engine",
    )
    supporting_evidence: List[UUID] = Field(
        default_factory=list,
        description="Event IDs affirming the selected outcome (provenance, sorted)",
    )
    contradicting_evidence: List[UUID] = Field(
        default_factory=list,
        description="Event IDs contradicting or complicating the selected outcome (sorted)",
    )
    alternative_explanations: List[str] = Field(
        default_factory=list,
        description="Enumerated alternative hypotheses not excluded by available evidence",
    )
    recommended_next_evidence: List[RecommendedEvidenceItem] = Field(
        default_factory=list,
        description="Deterministic, prioritised evidence acquisition recommendations",
    )
    explanation: Optional[AnalysisExplanation] = Field(
        default=None,
        description="Optional non-authoritative, evidence-grounded explanation.",
    )


class AnalysisTriggerRequest(BaseModel):
    """Request payload to initiate deterministic analysis."""

    rule_version: Optional[str] = Field(
        default=None,
        description="Optional rules version override; defaults to active version",
    )
    force_reanalysis: bool = Field(
        default=False,
        description="Re-run engine even if an existing analysis is cached",
    )
