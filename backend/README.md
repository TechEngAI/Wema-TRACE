# TRACE Backend Service

FastAPI-powered transaction investigation backend for TRACE. Built for deterministic evidence reconstruction across multi-institution payment logs.

## Core Responsibilities
- Houses the deterministic rules engine (Part 4), which maps canonical events and their correlation groups to one of the five exact outcomes and four qualitative strengths, with supporting/contradicting/missing evidence. It never computes probabilities or percentages.
- Houses the deterministic correlation engine (Part 3), which reliably links normalized events to payment attempts while preserving uncertainty. Correlation does **not** decide transaction outcomes.
- Ingests, validates, and normalizes synthetic payment logs (Part 2).
- Serves typed REST APIs for the React frontend.
- Optionally adds a constrained, evidence-grounded explanation to deterministic analysis responses (Part 5). It is disabled by default and never changes the finding.
- Generates read-only structured evidence packs (Part 6). Human investigator reviews remain planned because analysis snapshots are not yet persisted.

## Technology Stack
- **Framework**: FastAPI (Python 3.11+)
- **Server**: Uvicorn ASGI
- **Data Validation & Schemas**: Pydantic v2
- **Persistence Target**: PostgreSQL / Supabase
- **Testing**: Pytest & HTTPX TestClient

## Project Structure
```text
backend/
├── app/
│   ├── __init__.py
│   ├── config.py             # App settings via pydantic-settings
│   ├── main.py               # FastAPI entrypoint, CORS, /health, error handlers
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── router.py     # /cases, /evidence, /correlation, /analyze, /evidence-pack; 501 for /review
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py           # SQLAlchemy declarative base
│   │   ├── models.py         # CaseModel, SourceFileModel, RawRecordModel, EventModel
│   │   └── session.py        # Engine, SessionLocal, get_db, init_db
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── exceptions.py     # Domain exceptions (FileTooLarge, Duplicate, Validation)
│   │   ├── parsers.py        # Safe CSV & JSONL parsing
│   │   ├── validators.py     # Timezone, minor units, and institution schema validation
│   │   ├── normalizer.py     # Canonical event mapping with raw_record_id lineage
│   │   └── service.py        # Transactional ingestion orchestration
│   ├── correlation/
│   │   ├── __init__.py
│   │   ├── engine.py         # Deterministic multi-pass correlation (union-find over global ids)
│   │   └── exceptions.py     # CrossCaseCorrelationError & correlation domain errors
│   ├── rules/
│   │   ├── __init__.py
│   │   └── engine.py         # Deterministic outcome rules engine (5 outcomes, 4 strengths)
│   └── schemas/
│       ├── __init__.py
│       ├── enums.py          # 5 exact outcomes, 4 qualitative strengths
│       ├── analysis.py       # Shared AnalysisResponse & sub-schemas
│       ├── case.py           # Case models & requests
│       ├── correlation.py    # Correlation states, methods, reason codes, groups
│       ├── evidence.py       # Evidence ingestion & evidence-pack schemas
│       ├── health.py         # Health probe schemas
│       └── review.py         # Human reviewer models
├── migrations/
│   ├── 001_initial_schema.sql# PostgreSQL DDL migration (Supabase compatible)
│   └── README.md             # Migration execution instructions
├── tests/
│   ├── fixtures/             # Synthetic CSV and JSONL test fixtures
│   ├── __init__.py
│   ├── test_contracts.py     # Enum & domain contract tests
│   ├── test_health.py        # Health and OpenAPI route verification
│   ├── test_ingestion.py     # Comprehensive positive/negative ingestion tests
│   ├── test_correlation.py   # Deterministic correlation, ambiguity & determinism tests
│   └── test_rules.py         # Deterministic outcome rules, 10 scenarios & determinism tests
├── .env.example              # Safe placeholder environment config
├── pyproject.toml            # Build metadata & pytest configuration
├── requirements.txt          # Pinned dependency manifest
└── README.md
```

## Local Development Setup

### 1. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure real passwords, API keys, or secrets are NEVER checked into version control.

The optional explanation layer requires both `AI_EXPLANATIONS_ENABLED=true` and
`AI_API_KEY`. Without either, `/analyze` returns the same deterministic analysis
with a static, evidence-grounded explanation and makes no network call.

Generated prose is validated conservatively. UUID membership can establish only
that a record is in the permitted analysis scope; it cannot prove a free-form
claim about that record. Because arbitrary natural-language factual claims are
not reliably machine-verifiable, generated event/state assertions are rejected
and replaced with the deterministic static fallback.

## Evidence packs (Part 6)

`GET /api/v1/cases/{case_id}/evidence-pack` regenerates a read-only structured
JSON pack from the case's current persisted evidence, deterministic analysis,
correlation result, and validated explanation. Packs are not stored snapshots.
PDF export is not implemented.

## Human reviews (Part 8)

`POST /api/v1/cases/{case_id}/review` records a separate human assessment and a
server-calculated snapshot of the current deterministic finding. `GET
/api/v1/cases/{case_id}/reviews` lists prior reviews. Reviews never change the Part 4
outcome or strength, evidence, correlation, or email-draft behavior. Reviewer display
names are unverified metadata because this synthetic-data MVP has no authentication.

The `integrity.sha256` value is SHA-256 over UTF-8 JSON of the complete pack
with only `integrity.sha256` omitted, serialized with sorted keys and compact
separators. It detects changes to that representation; it is not a signature
and does not prove evidence truth, completeness, authenticity, or authorship.

### 2. Dependency Installation
Using standard pip (or within a virtual environment):
```bash
pip install -r requirements.txt
```

### 3. Run Development Server
```bash
python -m uvicorn app.main:app --reload --port 8000
```
- Interactive Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- Interactive ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- Health Probe: [http://localhost:8000/health](http://localhost:8000/health)

### 4. Run Test Suite
```bash
python -m pytest
```
