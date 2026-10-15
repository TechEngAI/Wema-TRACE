"""Typed human-review contracts; reviewer identity is unverified MVP metadata."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.enums import OutcomeEnum, ReviewDecisionEnum, StrengthEnum


class MachineFindingSnapshot(BaseModel):
    """Server-calculated deterministic finding captured when a review is recorded."""

    finding_reference: str = Field(description="SHA-256 reference of the canonical machine finding snapshot")
    outcome: OutcomeEnum
    strength: StrengthEnum
    rule_version: str
    category: str
    outcome_reason: str
    strength_reason: str
    scope_examined: str


class ReviewCreateRequest(BaseModel):
    reviewer_display_name: str = Field(min_length=1, max_length=100, description="Unverified reviewer-supplied display name")
    decision: ReviewDecisionEnum
    rationale: str = Field(min_length=1, max_length=4000)
    notes: Optional[str] = Field(default=None, max_length=4000)
    requested_additional_evidence: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("reviewer_display_name", "rationale", "notes")
    @classmethod
    def reject_blank_text(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("must not be blank or whitespace only")
        return value.strip() if value is not None else None

    @field_validator("requested_additional_evidence")
    @classmethod
    def validate_requested_evidence(cls, values: List[str]) -> List[str]:
        cleaned = []
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("requested additional evidence must not contain blank items")
            if len(item) > 500:
                raise ValueError("requested additional evidence items must be at most 500 characters")
            cleaned.append(item)
        return cleaned


class ReviewResponse(BaseModel):
    review_id: UUID
    case_id: UUID
    reviewer_display_name: str = Field(description="Unverified reviewer metadata; TRACE has no authentication")
    reviewer_identity_verified: bool = False
    decision: ReviewDecisionEnum
    rationale: str
    notes: Optional[str] = None
    requested_additional_evidence: List[str] = Field(default_factory=list)
    machine_finding: MachineFindingSnapshot
    created_at: datetime


class ReviewHistoryResponse(BaseModel):
    case_id: UUID
    reviews: List[ReviewResponse] = Field(default_factory=list)
