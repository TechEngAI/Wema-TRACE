"""Correlation Result Contracts and Typed Schemas.

Defines deterministic correlation group structures, status enums, method enums,
and structured reason codes.

CRITICAL RULES:
- Does NOT introduce or alter transaction outcome enums (DISCREPANCY_CONFIRMED, etc.).
- Does NOT use finding-strength labels (HIGH, MODERATE, etc.) to represent correlation confidence.
- Preserves full provenance, event IDs, raw record pointers, and temporal ambiguity flags.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field

from app.schemas.enums import EventActionEnum, EventStatusEnum, InstitutionPartyEnum


class CorrelationStatusEnum(str, Enum):
    """Deterministic status of an event linkage group.

    Strictly separate from investigation outcome enums.
    """

    CONFIRMED = "CONFIRMED"
    """Events are authoritatively linked via verified unique identifiers (session_id and/or RRN)
    or verified institution-scoped references without contradictory evidence."""

    CANDIDATE = "CANDIDATE"
    """Events share partial correlation attributes (e.g. amount and timestamp proximity within
    allowable skew, or shared reference without established uniqueness). Kept unmerged for audit review."""

    AMBIGUOUS = "AMBIGUOUS"
    """Material identifier conflict detected between records (e.g. matching session_id with differing
    non-null RRNs, or multiple distinct records colliding on non-unique identifiers)."""

    UNMATCHED = "UNMATCHED"
    """Standalone events with no corroborating links or evidence to other events within the case."""


class CorrelationMethodEnum(str, Enum):
    """The deterministic matching technique utilized to form the group.

    Identifier scope (documented, not assumed):
    - ``session_id`` and ``rrn`` are treated as GLOBAL interbank identifiers
      (NIBSS-style instant payment handoff identifiers).
    - ``reference`` is treated as an INSTITUTION-LOCAL identifier; a bare
      reference is never assumed to be globally unique.
    """

    SESSION_AND_RRN_MATCH = "SESSION_AND_RRN_MATCH"
    SESSION_ID_MATCH = "SESSION_ID_MATCH"
    RRN_MATCH = "RRN_MATCH"
    INSTITUTION_SCOPED_REFERENCE = "INSTITUTION_SCOPED_REFERENCE"
    TEMPORAL_AMOUNT_PROXIMITY = "TEMPORAL_AMOUNT_PROXIMITY"
    PARTIAL_REFERENCE_HEURISTIC = "PARTIAL_REFERENCE_HEURISTIC"
    NONE = "NONE"


class IdentifierScopeEnum(str, Enum):
    """Declared uniqueness scope of the identifiers that justified a group."""

    GLOBAL_INTERBANK = "GLOBAL_INTERBANK"
    INSTITUTION_LOCAL = "INSTITUTION_LOCAL"
    NONE = "NONE"


class CorrelationReasonCode(str, Enum):
    """Machine-readable reason code explaining the exact matching determination."""

    STRONG_SESSION_AND_RRN_CONSISTENT = "STRONG_SESSION_AND_RRN_CONSISTENT"
    STRONG_SESSION_ID_CONSISTENT = "STRONG_SESSION_ID_CONSISTENT"
    STRONG_RRN_CONSISTENT = "STRONG_RRN_CONSISTENT"
    SECONDARY_INSTITUTION_REFERENCE_VERIFIED = "SECONDARY_INSTITUTION_REFERENCE_VERIFIED"
    AMBIGUOUS_IDENTIFIER_CONFLICT = "AMBIGUOUS_IDENTIFIER_CONFLICT"
    AMBIGUOUS_MULTIPLE_CANDIDATE_COLLISION = "AMBIGUOUS_MULTIPLE_CANDIDATE_COLLISION"
    CANDIDATE_TEMPORAL_AMOUNT_PROXIMITY = "CANDIDATE_TEMPORAL_AMOUNT_PROXIMITY"
    CANDIDATE_PARTIAL_REFERENCE_ONLY = "CANDIDATE_PARTIAL_REFERENCE_ONLY"
    INSUFFICIENT_IDENTIFIERS = "INSUFFICIENT_IDENTIFIERS"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    TIMESTAMP_OUT_OF_TOLERANCE = "TIMESTAMP_OUT_OF_TOLERANCE"
    CROSS_CASE_BOUNDARY_VIOLATION = "CROSS_CASE_BOUNDARY_VIOLATION"


class CorrelationEventRef(BaseModel):
    """Lightweight representation of an event in a correlation group with provenance."""

    event_id: UUID = Field(description="Unique event identifier")
    raw_record_id: UUID = Field(description="Pointer to immutable raw source record")
    party: InstitutionPartyEnum = Field(description="Institution party of this event")
    action: EventActionEnum = Field(description="Canonical event action")
    status: EventStatusEnum = Field(description="Canonical event status")
    amount_minor_units: int = Field(description="Monetary amount in integer minor units (kobo)")
    currency: str = Field(description="Currency code (e.g. NGN)")
    session_id: Optional[str] = Field(default=None, description="Session ID if present")
    rrn: Optional[str] = Field(default=None, description="Retrieval Reference Number if present")
    reference: Optional[str] = Field(default=None, description="Institution transaction reference")
    event_timestamp: datetime = Field(description="Observed source timestamp with explicit timezone")


class CorrelationGroup(BaseModel):
    """A deterministic group of correlated, candidate, ambiguous, or unmatched events."""

    group_id: str = Field(description="Deterministic unique group identifier")
    status: CorrelationStatusEnum = Field(description="Correlation linkage status")
    method: CorrelationMethodEnum = Field(description="Algorithm method applied")
    identifier_scope: IdentifierScopeEnum = Field(
        default=IdentifierScopeEnum.NONE,
        description="Declared uniqueness scope of the primary identifying key(s)",
    )
    reason_code: CorrelationReasonCode = Field(description="Structured machine-readable reason code")
    reason_description: str = Field(description="Human-readable explanation of the correlation decision")
    member_event_ids: List[UUID] = Field(
        default_factory=list,
        description="List of constituent event IDs sorted deterministically",
    )
    events: List[CorrelationEventRef] = Field(
        default_factory=list,
        description="Constituent event summaries preserving provenance",
    )
    identifying_keys: Dict[str, Any] = Field(
        default_factory=dict,
        description="Keys that justified the group (e.g. session_id, rrn, reference)",
    )
    temporal_ordering_ambiguous: bool = Field(
        default=False,
        description="True if timestamps within ±5s tolerance or out-of-order prevent unambiguous sequencing",
    )
    duplicate_identity_event_ids: List[UUID] = Field(
        default_factory=list,
        description=(
            "Event IDs sharing an identical (party, action, amount, currency, session_id, rrn, "
            "reference, timestamp) signature within this group; indicates probable duplicate records "
            "rather than distinct transaction legs. Sorted deterministically."
        ),
    )
    contradictions: List[str] = Field(
        default_factory=list,
        description="Specific conflicting fields or values detected between member events",
    )


class CaseCorrelationResult(BaseModel):
    """Comprehensive correlation result for a target case."""

    case_id: UUID = Field(description="Target case UUID")
    algorithm_version: str = Field(default="2026.1.0-corr", description="Correlation algorithm version")
    computed_at: datetime = Field(description="Calculation timestamp")
    total_events: int = Field(description="Total canonical events evaluated")
    groups: List[CorrelationGroup] = Field(
        default_factory=list,
        description="Deterministic list of correlation groups sorted by status and timestamp",
    )
    summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Count breakdown of groups by status (confirmed, candidate, ambiguous, unmatched)",
    )
