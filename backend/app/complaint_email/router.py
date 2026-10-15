"""TRACE Complaint Email API Router.

Provides the isolated Part 7 endpoint:
POST /cases/{case_id}/complaint-email

Loads authoritative case details and rules-engine analysis from trusted backend services.
Never accepts client-supplied outcomes or arbitrary timelines.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.complaint_email.service import build_complaint_email_service
from app.correlation.engine import correlate_events
from app.db.models import CaseModel, EventModel
from app.db.session import get_db
from app.rules.engine import evaluate_case
from app.schemas.complaint_email import (
    ComplaintEmailDraftRequest,
    ComplaintEmailDraftResponse,
)

router = APIRouter(tags=["complaint-email"])
complaint_email_service = build_complaint_email_service()


@router.post(
    "/cases/{case_id}/complaint-email",
    response_model=ComplaintEmailDraftResponse,
    status_code=status.HTTP_200_OK,
    summary="Draft Investigation / Complaint Email",
    description=(
        "Drafts a professional, evidence-grounded investigation or complaint email "
        "based on the case's authoritative deterministic analysis. "
        "This is a drafting assistant only: no emails are sent and no financial commitments are made."
    ),
)
async def draft_complaint_email(
    case_id: UUID,
    payload: ComplaintEmailDraftRequest,
    db: Session = Depends(get_db),
) -> ComplaintEmailDraftResponse:
    """Draft an evidence-grounded investigation email for a persisted dispute case."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dispute case with ID '{case_id}' was not found.",
        )

    # Load canonical events and run deterministic evaluation
    events = (
        db.query(EventModel)
        .filter(EventModel.case_id == case_id)
        .order_by(EventModel.event_timestamp, EventModel.id)
        .all()
    )

    correlation = correlate_events(events=events, case_id=case_id)
    analysis = evaluate_case(case=case, events=events, correlation=correlation)

    return complaint_email_service.draft_email(
        case=case,
        analysis=analysis,
        request=payload,
    )
