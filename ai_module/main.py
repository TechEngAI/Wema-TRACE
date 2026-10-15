from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from .evidence_exporter import EvidenceExporter
    from .explanation_service import AIExplanationService
except ImportError:
    from evidence_exporter import EvidenceExporter
    from explanation_service import AIExplanationService


class AnalysisRequest(BaseModel):
    case_id: str
    outcome: str
    strength: Union[str, float]
    timeline: List[Any]
    supporting_event_ids: List[Union[str, int]]
    session_reference: Optional[str] = None
    amount: Optional[Union[str, int, float]] = None
    investigation_scope: Optional[str] = None
    timestamps: Dict[str, Any] = Field(default_factory=dict)
    rule_version: Optional[str] = None
    supporting_evidence: List[Any] = Field(default_factory=list)
    contradicting_evidence: List[Any] = Field(default_factory=list)
    missing_evidence: List[Any] = Field(default_factory=list)
    data_quality_flags: List[Any] = Field(default_factory=list)
    ai_metadata: Dict[str, Any] = Field(default_factory=dict)


class ExplanationResponse(BaseModel):
    case_id: str
    outcome: str
    explanation: str


app = FastAPI(title="TRACE AI Backend", version="1.0.0")
explanation_service = AIExplanationService()
evidence_exporter = EvidenceExporter()


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request payload is invalid.",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error = detail
    else:
        error = {
            "code": "http_error",
            "message": str(detail),
        }
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "The request could not be processed.",
            }
        },
    )


def _analysis_dict(payload: AnalysisRequest) -> Dict[str, Any]:
    dump = getattr(payload, "model_dump", None)
    data = dump(exclude_none=True) if dump else payload.dict(exclude_none=True)
    data["case_reference"] = data.get("case_reference", payload.case_id)
    return data


@app.post("/api/v1/explain", response_model=ExplanationResponse)
async def explain(payload: AnalysisRequest) -> ExplanationResponse:
    analysis = _analysis_dict(payload)
    try:
        explanation = explanation_service.explain(analysis)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "explanation_error", "message": "Could not generate explanation."},
        ) from exc

    return ExplanationResponse(
        case_id=payload.case_id,
        outcome=payload.outcome,
        explanation=explanation,
    )


@app.post("/api/v1/export-evidence")
async def export_evidence(payload: AnalysisRequest) -> Dict[str, Any]:
    if not payload.session_reference or payload.amount is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_transaction_details",
                "message": "Exact session_reference and amount are required for the complaint email draft.",
            },
        )

    analysis = _analysis_dict(payload)
    ai_metadata = dict(payload.ai_metadata)
    if not ai_metadata.get("explanation"):
        ai_metadata["explanation"] = explanation_service.explain(analysis)

    try:
        pack = evidence_exporter.build_evidence_pack(
            analysis,
            payload.timeline,
            payload.missing_evidence,
            ai_metadata,
            session_reference=payload.session_reference,
            amount=payload.amount,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "evidence_export_error", "message": "Could not create the evidence pack."},
        ) from exc

    return pack