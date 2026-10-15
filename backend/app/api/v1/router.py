"""TRACE API v1 Router.

Implements:
- Health check endpoints
- Minimum case persistence (POST /api/v1/cases, GET /api/v1/cases/{case_id}, GET /api/v1/cases)
- Evidence ingestion (POST /api/v1/cases/{case_id}/evidence)
- Deterministic correlation (GET /api/v1/cases/{case_id}/correlation)
- Deterministic analysis (POST /api/v1/cases/{case_id}/analyze)
Future endpoints (evidence-pack, review) return HTTP 501 Not Implemented.
"""

from datetime import datetime, timezone
import hashlib
import json
from typing import Optional
from uuid import UUID
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CaseModel, CaseReviewModel, EventModel, SourceFileModel
from app.db.session import get_db
from app.correlation.engine import correlate_events
from app.ingestion.exceptions import IngestionError
from app.ingestion.service import ingest_evidence
from app.rules.engine import evaluate_case
from app.explanations.service import build_explanation_service
from app.evidence_pack.service import build_evidence_pack
from app.complaint_email.router import router as complaint_email_router
from app.schemas import (
    AnalysisResponse,
    AnalysisTriggerRequest,
    CaseCorrelationResult,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseListItem,
    CaseListResponse,
    CaseResponse,
    CaseStatusEnum,
    EvidencePackResponse,
    EvidenceUploadResponse,
    HealthResponse,
    InstitutionPartyEnum,
    ReviewCreateRequest,
    ReviewHistoryResponse,
    ReviewResponse,
    MachineFindingSnapshot,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])
explanation_service = build_explanation_service()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="API v1 Health Probe",
    description="Returns current service status and version.",
)
async def get_v1_health() -> HealthResponse:
    """Check API v1 service availability."""
    return HealthResponse(
        status="healthy",
        service="trace-backend",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc),
        environment=settings.ENVIRONMENT,
    )


def _machine_finding_snapshot(analysis: AnalysisResponse) -> MachineFindingSnapshot:
    """Create a stable reference and a small immutable-at-write-time review snapshot."""
    snapshot = {
        "outcome": analysis.outcome.value,
        "strength": analysis.strength.value,
        "rule_version": analysis.rule_version,
        "category": analysis.category,
        "outcome_reason": analysis.outcome_reason,
        "strength_reason": analysis.strength_reason,
        "scope_examined": analysis.scope_examined,
    }
    reference = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return MachineFindingSnapshot(finding_reference=reference, **snapshot)


def _review_response(review: CaseReviewModel) -> ReviewResponse:
    return ReviewResponse(
        review_id=review.id,
        case_id=review.case_id,
        reviewer_display_name=review.reviewer_display_name,
        decision=review.decision,
        rationale=review.rationale,
        notes=review.notes,
        requested_additional_evidence=review.requested_additional_evidence,
        machine_finding=MachineFindingSnapshot.model_validate(review.machine_finding_snapshot),
        created_at=review.created_at,
    )


@router.post(
    "/cases",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Dispute Case",
    description="Registers a new disputed transaction investigation case.",
)
async def create_case(
    payload: CaseCreateRequest,
    db: Session = Depends(get_db),
) -> CaseResponse:
    """Register and persist a new dispute investigation case."""
    existing = db.query(CaseModel).filter(CaseModel.reference == payload.reference).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dispute case with reference '{payload.reference}' already exists.",
        )

    case_id = uuid.uuid4()
    new_case = CaseModel(
        id=case_id,
        reference=payload.reference,
        initiating_party=payload.initiating_party.value,
        counterparty=payload.counterparty.value,
        disputed_amount=payload.disputed_amount,
        currency=payload.currency,
        status=CaseStatusEnum.OPEN.value,
        session_id=payload.session_id,
        rrn=payload.rrn,
        narrative=payload.narrative,
        metadata_json=payload.metadata,
    )

    db.add(new_case)
    db.commit()
    db.refresh(new_case)

    return CaseResponse(
        case_id=new_case.id,
        reference=new_case.reference,
        initiating_party=InstitutionPartyEnum(new_case.initiating_party),
        counterparty=InstitutionPartyEnum(new_case.counterparty),
        disputed_amount=new_case.disputed_amount,
        currency=new_case.currency,
        status=CaseStatusEnum(new_case.status),
        created_at=new_case.created_at,
        metadata=new_case.metadata_json,
    )


@router.get(
    "/cases",
    response_model=CaseListResponse,
    summary="List Cases",
    description="Queries paginated dispute cases with optional status filtering.",
)
async def list_cases(
    status_filter: Optional[CaseStatusEnum] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CaseListResponse:
    """Query paginated dispute cases."""
    query = db.query(CaseModel)
    if status_filter:
        query = query.filter(CaseModel.status == status_filter.value)

    total = query.count()
    offset = (page - 1) * page_size
    cases = query.order_by(CaseModel.created_at.desc()).offset(offset).limit(page_size).all()

    items = [
        CaseListItem(
            case_id=c.id,
            reference=c.reference,
            initiating_party=InstitutionPartyEnum(c.initiating_party),
            counterparty=InstitutionPartyEnum(c.counterparty),
            disputed_amount=c.disputed_amount,
            currency=c.currency,
            status=CaseStatusEnum(c.status),
            created_at=c.created_at,
            latest_outcome=None,
            latest_strength=None,
        )
        for c in cases
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return CaseListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetailResponse,
    summary="Get Case Details",
    description="Retrieves comprehensive case timeline, events count, and details.",
)
async def get_case(
    case_id: UUID,
    db: Session = Depends(get_db),
) -> CaseDetailResponse:
    """Retrieve case details and ingestion count."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )

    events_count = db.query(EventModel).filter(EventModel.case_id == case_id).count()

    return CaseDetailResponse(
        case_id=case.id,
        reference=case.reference,
        initiating_party=InstitutionPartyEnum(case.initiating_party),
        counterparty=InstitutionPartyEnum(case.counterparty),
        disputed_amount=case.disputed_amount,
        currency=case.currency,
        status=CaseStatusEnum(case.status),
        created_at=case.created_at,
        metadata=case.metadata_json,
        events_count=events_count,
        latest_analysis=None,
    )


@router.post(
    "/cases/{case_id}/evidence",
    response_model=EvidenceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest Case Evidence",
    description="Ingests, validates, and normalizes synthetic transaction logs/records for the specified case.",
)
async def upload_evidence(
    case_id: UUID,
    file: UploadFile = File(..., description="CSV or JSONL evidence file"),
    institution_source: InstitutionPartyEnum = Form(..., description="Originating institution"),
    file_type: Optional[str] = Form(None, description="Optional explicit file type (CSV or JSONL)"),
    db: Session = Depends(get_db),
) -> EvidenceUploadResponse:
    content = await file.read()
    return ingest_evidence(
        db=db,
        case_id=case_id,
        file_bytes=content,
        filename=file.filename or "unknown_evidence.csv",
        institution_source=institution_source,
        file_type_hint=file_type,
    )


@router.get(
    "/cases/{case_id}/correlation",
    response_model=CaseCorrelationResult,
    summary="Get Case Transaction Correlation",
    description="Deterministically identifies event linkages, candidate groups, and identifier ambiguities.",
)
async def get_case_correlation(
    case_id: UUID,
    db: Session = Depends(get_db),
) -> CaseCorrelationResult:
    """Execute deterministic correlation on normalized events for the case."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )

    events = db.query(EventModel).filter(EventModel.case_id == case_id).all()
    return correlate_events(events=events, case_id=case_id)


@router.post(
    "/cases/{case_id}/analyze",
    response_model=AnalysisResponse,
    summary="Trigger Deterministic Analysis",
    description="Runs deterministic rules engine against canonical normalized events.",
)
async def analyze_case(
    case_id: UUID,
    payload: Optional[AnalysisTriggerRequest] = None,
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    """Execute the deterministic rules engine over canonical events for the case."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )

    events = (
        db.query(EventModel)
        .filter(EventModel.case_id == case_id)
        .order_by(EventModel.event_timestamp, EventModel.party, EventModel.id)
        .all()
    )
    analysis = evaluate_case(case=case, events=events)
    return analysis.model_copy(update={"explanation": explanation_service.explain(analysis)})


@router.get(
    "/cases/{case_id}/evidence-pack",
    response_model=EvidencePackResponse,
    summary="Export Evidence Pack",
    description="Generates an immutable, cryptographic audit package for the case.",
)
async def get_evidence_pack(
    case_id: UUID,
    db: Session = Depends(get_db),
) -> EvidencePackResponse:
    """Regenerate a read-only structured evidence pack from current case records."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )
    events = (
        db.query(EventModel)
        .filter(EventModel.case_id == case_id)
        .order_by(EventModel.event_timestamp, EventModel.id)
        .all()
    )
    source_files = db.query(SourceFileModel).filter(SourceFileModel.case_id == case_id).all()
    correlation = correlate_events(events=events, case_id=case_id)
    analysis = evaluate_case(case=case, events=events, correlation=correlation)
    analysis = analysis.model_copy(update={"explanation": explanation_service.explain(analysis)})
    return build_evidence_pack(
        case=case,
        events=events,
        source_files=source_files,
        analysis=analysis,
        correlation=correlation,
    )


@router.post(
    "/cases/{case_id}/review",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record Investigator Review",
    description="Records human investigator sign-off or verdict on deterministic findings.",
)
async def create_review(
    case_id: UUID, payload: ReviewCreateRequest, db: Session = Depends(get_db)
) -> ReviewResponse:
    """Persist a human assessment without changing the authoritative machine finding."""
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case with ID '{case_id}' was not found.")
    events = db.query(EventModel).filter(EventModel.case_id == case_id).order_by(
        EventModel.event_timestamp, EventModel.party, EventModel.id
    ).all()
    machine_finding = _machine_finding_snapshot(evaluate_case(case=case, events=events))
    review = CaseReviewModel(
        case_id=case_id,
        reviewer_display_name=payload.reviewer_display_name,
        decision=payload.decision.value,
        rationale=payload.rationale,
        notes=payload.notes,
        requested_additional_evidence=payload.requested_additional_evidence,
        finding_reference=machine_finding.finding_reference,
        machine_finding_snapshot=machine_finding.model_dump(mode="json"),
    )
    try:
        db.add(review)
        db.commit()
        db.refresh(review)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to record the review.")
    return _review_response(review)


@router.get(
    "/cases/{case_id}/reviews",
    response_model=ReviewHistoryResponse,
    summary="List Human Review History",
    description="Lists separate human assessments and their server-calculated machine finding snapshots.",
)
async def list_reviews(case_id: UUID, db: Session = Depends(get_db)) -> ReviewHistoryResponse:
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case with ID '{case_id}' was not found.")
    reviews = db.query(CaseReviewModel).filter(CaseReviewModel.case_id == case_id).order_by(
        CaseReviewModel.created_at.asc(), CaseReviewModel.id.asc()
    ).all()
    return ReviewHistoryResponse(case_id=case_id, reviews=[_review_response(review) for review in reviews])


router.include_router(complaint_email_router)
