"""TRACE Backend Application Entrypoint.

Transaction Reconciliation & Analysis Platform for multi-party payment investigations.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as api_v1_router
from app.config import settings
from app.correlation.exceptions import CorrelationError
from app.db.session import init_db
from app.ingestion.exceptions import IngestionError
from app.schemas.health import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize database schemas."""
    init_db()
    yield


app = FastAPI(
    title="TRACE API",
    description=(
        "TRACE: Transaction Reconciliation & Analysis Platform. "
        "Investigates disputed payment outcomes through synthetic multi-institution evidence reconstruction. "
        "All findings are strictly governed by a deterministic rules engine."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Exception handler for safe, client-actionable ingestion errors
@app.exception_handler(IngestionError)
async def ingestion_error_handler(request: Request, exc: IngestionError):
    """Transform domain ingestion errors into safe HTTP JSON responses without stack traces."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "details": exc.details,
        },
    )

# Exception handler for deterministic correlation domain errors (e.g. cross-case).
@app.exception_handler(CorrelationError)
async def correlation_error_handler(request: Request, exc: CorrelationError):
    """Transform correlation domain errors into safe HTTP JSON responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "details": exc.details,
        },
    )


# CORS configuration for local React / Vite frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(api_v1_router)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["system"],
    summary="Root Service Health Probe",
    description="Returns global service health, environment, and current timestamp.",
)
async def root_health() -> HealthResponse:
    """Service liveness probe."""
    return HealthResponse(
        status="healthy",
        service="trace-backend",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc),
        environment=settings.ENVIRONMENT,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
