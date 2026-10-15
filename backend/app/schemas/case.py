"""Case request and response schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field

from app.schemas.analysis import AnalysisResponse
from app.schemas.enums import CaseStatusEnum, InstitutionPartyEnum, OutcomeEnum, StrengthEnum


class CaseCreateRequest(BaseModel):
    """Payload to create a new dispute investigation case."""

    reference: str = Field(description="Unique dispute tracking reference (e.g. TRC-2026-1001)")
    initiating_party: InstitutionPartyEnum = Field(description="Institution initiating the case")
    counterparty: InstitutionPartyEnum = Field(description="Opposing institution")
    disputed_amount: int = Field(
        gt=0,
        description="Disputed monetary value in integer minor units (e.g., 8500000 kobo for ₦85,000)",
    )
    currency: str = Field(default="NGN", max_length=3, description="3-character currency code")
    session_id: Optional[str] = Field(default=None, description="Known session ID correlation reference")
    rrn: Optional[str] = Field(default=None, description="Retrieval Reference Number")
    narrative: Optional[str] = Field(default=None, description="Dispute context or customer claim description")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary context metadata")


class CaseResponse(BaseModel):
    """Case summary returned after creation or quick lookup."""

    case_id: UUID = Field(description="Unique system case UUID")
    reference: str = Field(description="Unique dispute reference")
    initiating_party: InstitutionPartyEnum = Field(description="Initiating institution")
    counterparty: InstitutionPartyEnum = Field(description="Counterparty institution")
    disputed_amount: int = Field(description="Disputed amount in minor units")
    currency: str = Field(description="Currency code")
    status: CaseStatusEnum = Field(description="Current investigation lifecycle status")
    created_at: datetime = Field(description="Timestamp of case creation")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Case metadata")


class CaseListItem(BaseModel):
    """Item representation in case list queries."""

    case_id: UUID
    reference: str
    initiating_party: InstitutionPartyEnum
    counterparty: InstitutionPartyEnum
    disputed_amount: int
    currency: str
    status: CaseStatusEnum
    created_at: datetime
    latest_outcome: Optional[OutcomeEnum] = None
    latest_strength: Optional[StrengthEnum] = None


class CaseListResponse(BaseModel):
    """Paginated response for case queries."""

    items: List[CaseListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class CaseDetailResponse(BaseModel):
    """Comprehensive case view including associated analysis."""

    case_id: UUID
    reference: str
    initiating_party: InstitutionPartyEnum
    counterparty: InstitutionPartyEnum
    disputed_amount: int
    currency: str
    status: CaseStatusEnum
    created_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)
    events_count: int = 0
    latest_analysis: Optional[AnalysisResponse] = None
