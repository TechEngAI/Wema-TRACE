"""Complaint and investigation email drafting schemas.

Strictly defines request and response contracts for Part 7.
Client-supplied outcomes, strength values, or arbitrary timelines are NEVER accepted.
All drafts are explicitly marked as requiring human review.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.schemas.enums import InstitutionPartyEnum, OutcomeEnum, StrengthEnum


class ComplaintEmailPurposeEnum(str, Enum):
    """Investigation and complaint email purpose classifications."""

    REQUEST_MISSING_EVIDENCE = "REQUEST_MISSING_EVIDENCE"
    INVESTIGATE_CONFLICTING_STATUSES = "INVESTIGATE_CONFLICTING_STATUSES"
    CLARIFY_SUSPECTED_DISCREPANCY = "CLARIFY_SUSPECTED_DISCREPANCY"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"


class ComplaintEmailDraftRequest(BaseModel):
    """Client request to generate an email draft for a persisted case.

    Notice: Does NOT accept outcome, strength, timeline, or findings.
    Authoritative analysis is always loaded from trusted TRACE backend services.
    """

    recipient_institution: InstitutionPartyEnum = Field(
        description="Target institution receiving the investigation or clarification request."
    )
    recipient_role: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional recipient department or role (e.g. 'Settlement Operations', 'Dispute Support').",
    )
    recipient_email: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional recipient email address for draft To-header display.",
    )
    purpose: Optional[ComplaintEmailPurposeEnum] = Field(
        default=None,
        description="Explicit drafting purpose. If omitted, deterministically mapped from authoritative analysis.",
    )
    additional_context: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional operator note or context. Treated as untrusted input; screened for injection.",
    )

    @field_validator("additional_context")
    @classmethod
    def validate_additional_context(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        # Basic prompt injection checks for common adversarial phrases
        lowered = cleaned.lower()
        adversarial_patterns = [
            "ignore previous instructions",
            "ignore all instructions",
            "system prompt",
            "override instructions",
            "you are now",
            "jailbreak",
            "<script",
        ]
        if any(pat in lowered for pat in adversarial_patterns):
            raise ValueError("additional_context contains unsupported or unsafe instruction patterns.")
        return cleaned


class ComplaintEmailDraftResponse(BaseModel):
    """Read-only email draft grounded in authoritative case analysis.

    This contract is an operations drafting assistant output only.
    It contains no side effects and must be visibly labeled as a draft requiring human review.
    """

    draft_id: UUID = Field(default_factory=uuid4, description="Unique identifier of this generated draft")
    case_id: UUID = Field(description="Target TRACE case ID")
    case_reference: str = Field(description="TRACE case reference number")
    recipient_institution: InstitutionPartyEnum = Field(description="Target institution")
    recipient_role: Optional[str] = Field(default=None, description="Intended recipient team or role")
    recipient_email: Optional[str] = Field(default=None, description="Draft recipient email address")
    purpose: ComplaintEmailPurposeEnum = Field(description="Purpose of this investigation email")
    subject: str = Field(description="Clear, professional subject line with transaction identifiers")
    greeting: str = Field(description="Neutral professional greeting")
    body: str = Field(description="Complete formatted email body ready for human review")
    factual_summary: str = Field(description="Authoritative factual summary grounded in rules-engine finding")
    discrepancy_details: str = Field(description="Specific discrepancy or unresolved question")
    evidence_summary: str = Field(description="Evidence currently available, without overstating proof")
    requested_records: List[str] = Field(
        default_factory=list, description="Specific records or confirmations being requested"
    )
    closing: str = Field(description="Professional closing sign-off")

    # Authoritative immutable findings
    authoritative_outcome: OutcomeEnum = Field(description="Authoritative outcome from Part 4 rules engine")
    authoritative_strength: StrengthEnum = Field(description="Authoritative strength from Part 4 rules engine")

    # Non-negotiable review and safety markers
    is_draft: bool = Field(default=True, description="Always True: must never be automatically sent")
    requires_human_review: bool = Field(default=True, description="Always True: requires human operator sign-off")
    disclaimer: str = Field(
        default=(
            "DRAFT ONLY - REQUIRES HUMAN REVIEW BEFORE TRANSMISSION. "
            "TRACE is an investigation aid. This draft does not prove that unobserved events did not occur, "
            "does not execute payments or transfers, and does not constitute a financial commitment or legal accusation."
        ),
        description="Mandatory operational disclaimer",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of draft generation",
    )
    generator: str = Field(
        description="Generator mechanism: 'deterministic_fallback' or 'ai_assisted'"
    )
    data_quality_caveats: List[str] = Field(
        default_factory=list,
        description="Data quality caveats identified by analysis",
    )
