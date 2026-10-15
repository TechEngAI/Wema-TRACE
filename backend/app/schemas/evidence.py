"""Evidence ingestion and Part 6 evidence-pack export schemas."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.analysis import (
    AnalysisExplanation,
    DataQualityFlagItem,
    MissingEvidenceItem,
    RecommendedEvidenceItem,
)
from app.schemas.correlation import CorrelationGroup
from app.schemas.enums import (
    EventActionEnum,
    EventStatusEnum,
    InstitutionPartyEnum,
    OutcomeEnum,
    StrengthEnum,
)


class EvidenceUploadResponse(BaseModel):
    source_file_id: UUID
    case_id: UUID
    filename: str
    records_ingested: int
    sha256_hash: str
    status: str = "INGESTED"


class RawFileHashItem(BaseModel):
    institution: InstitutionPartyEnum
    filename: str
    sha256: str


class EvidencePackTimelineEvent(BaseModel):
    """Normalized event with provenance; raw payload contents are intentionally absent."""

    event_id: UUID
    event_timestamp: datetime
    institution: InstitutionPartyEnum
    action: EventActionEnum
    status: EventStatusEnum
    amount_minor_units: int
    currency: str
    session_id: Optional[str] = None
    rrn: Optional[str] = None
    reference: Optional[str] = None
    raw_record_id: UUID
    source_file_id: Optional[UUID] = None
    data_quality_flags: List[str] = Field(default_factory=list)


class EvidencePackSummary(BaseModel):
    outcome: OutcomeEnum
    strength: StrengthEnum
    outcome_reason: str
    strength_reason: str
    scope_examined: str
    disputed_amount_minor_units: int
    currency: str
    case_session_id: Optional[str] = None
    case_rrn: Optional[str] = None
    identifier_source: str = "CASE_RECORD"


class EvidencePackCorrelation(BaseModel):
    algorithm_version: str
    summary: dict[str, int] = Field(default_factory=dict)
    groups: List[CorrelationGroup] = Field(default_factory=list)


class EvidencePackEvidence(BaseModel):
    supporting_event_ids: List[UUID] = Field(default_factory=list)
    contradicting_event_ids: List[UUID] = Field(default_factory=list)
    missing_evidence: List[MissingEvidenceItem] = Field(default_factory=list)
    alternative_explanations: List[str] = Field(default_factory=list)
    recommended_next_evidence: List[RecommendedEvidenceItem] = Field(default_factory=list)


class EvidencePackIntegrity(BaseModel):
    algorithm: str = "SHA-256"
    sha256: str
    hashed_representation: str = (
        "UTF-8 JSON of the complete pack with integrity.sha256 omitted, serialized "
        "with lexicographically sorted keys and compact separators."
    )
    limitation: str = (
        "This hash detects changes to the hashed representation only; it is not a digital "
        "signature and does not prove evidence truth, completeness, authenticity, or authorship."
    )


class EvidencePackResponse(BaseModel):
    """Read-only structured investigation pack regenerated from current case evidence."""

    pack_schema_version: str = "2026.1.0-evidence-pack"
    pack_id: UUID
    case_id: UUID
    case_reference: str
    generated_at: datetime
    analysis_rule_version: str
    explanation_status: str = "AVAILABLE"
    investigation_aid_notice: str = (
        "TRACE is an evidence-based investigation aid. This pack is not proof of financial "
        "settlement and does not guarantee evidence completeness."
    )
    summary: EvidencePackSummary
    correlation: EvidencePackCorrelation
    timeline: List[EvidencePackTimelineEvent] = Field(default_factory=list)
    evidence: EvidencePackEvidence
    data_quality_flags: List[DataQualityFlagItem] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    explanation: AnalysisExplanation
    raw_file_hashes: List[RawFileHashItem] = Field(default_factory=list)
    integrity: EvidencePackIntegrity
