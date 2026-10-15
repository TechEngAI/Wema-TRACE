# TRACE — Transaction Reconciliation & Dispute Analysis Platform

**Hackathon Phase:** Parts 1–4 — Contracts, Ingestion, Correlation & Deterministic Rules Engine  
**Status:** Completed & Verified  

---

## 1. Project Overview

**TRACE** investigates disputed or uncertain electronic payment outcomes across multi-institution payment rails (originating bank, switch/gateway, receiving bank, and merchant). In real-world interbank payments (e.g. NIBSS Instant Payments, card switches), transactions can get stuck in indeterminate states due to timeout cascades, dropped callbacks, un-reversed debits, or ledger desynchronization.

TRACE reconstructs the end-to-end evidence chain from synthetic institutional records, determines root causes, and generates cryptographic, audit-ready evidence packs.

### Architectural Invariants
1. **Deterministic Authority**: A deterministic rules engine owns **100%** of all analysis findings, classifications, outcomes, and strengths.
2. **AI as Explainer Only**: Downstream AI components only provide natural-language explanations of frozen, deterministic findings. AI cannot alter findings, classifications, strengths, or rule versions.
3. **Observation & Audit Only**: TRACE **never** initiates transfers, refunds, reversals, credits, or settlements. It has no write access to bank ledgers or settlement accounts.
4. **Synthetic Data Sandbox**: All data used in hackathon demos and local environments is synthetic. No live bank integrations or credentials are used.

---

## 2. Target Architecture

TRACE is designed as a single backend service and a single relational database, paired with a React Single-Page Application (SPA):

```text
TRACE Workspace
├── docs/
│   ├── architecture.md       # High-level architecture, 10 rules, schema proposal
│   └── api-contract.md       # Shared domain contracts, exact enums, planned routes
├── backend/
│   ├── app/
│   │   ├── api/v1/router.py  # Planned /api/v1 routes + /health
│   │   ├── schemas/          # Pydantic v2 domain schemas & exact enums
│   │   ├── config.py         # App configuration via pydantic-settings
│   │   └── main.py           # FastAPI entrypoint & CORS middleware
│   ├── tests/
│   │   ├── test_contracts.py   # Tests for exact enums, schemas, validation
│   │   ├── test_health.py      # Tests for health probes & OpenAPI routes
│   │   ├── test_ingestion.py   # Ingestion & normalization tests (Part 2)
│   │   ├── test_correlation.py # Deterministic correlation tests (Part 3)
│   │   └── test_rules.py       # Deterministic outcome rules tests (Part 4)
│   ├── .env.example          # Safe backend configuration placeholder
│   ├── pyproject.toml        # Build metadata & pytest config
│   ├── requirements.txt      # Backend Python dependencies
│   └── README.md
├── frontend/
│   ├── src/
│   │   ├── services/api.ts   # Typed API client & health check
│   │   ├── types/contracts.ts# TypeScript representations of frozen domain contracts
│   │   ├── App.tsx           # Foundation status dashboard (no premature UI)
│   │   ├── index.css         # Modern design tokens & layout
│   │   └── main.tsx          # React application mount
│   ├── .env.example          # Safe frontend configuration placeholder
│   ├── package.json          # Node dependencies & build scripts
│   ├── vite.config.ts        # Vite configuration
│   └── README.md
├── .env.example              # Root environment template
├── .gitignore                # Git ignore rules protecting secrets and build artifacts
└── README.md
```

### Excluded Unnecessary Infrastructure
In strict compliance with architectural guidelines, the following are deliberately omitted:
- No Microservices (single FastAPI service avoids distributed consistency overhead)
- No Message Brokers / Kafka / RabbitMQ (bounded in-process execution)
- No WebSockets (predictable REST polling / state queries)
- No Machine Learning Anomaly Detection (non-deterministic; violates auditability)
- No ISO 8583 Binary Parsers (tabular synthetic CSV/JSON files are used)

---

## 3. Non-Negotiable Data and Engineering Rules

1. **Integer Minor Units**: All monetary values use integer minor units; ₦85,000 is represented as `8500000` kobo. Floating-point currency is strictly prohibited.
2. **ISO 8601 with Explicit Offsets (Tolerance Never Manufactures Order)**: Timestamps require explicit timezone offsets (e.g., `2026-10-15T00:15:30.500+01:00`). Source timestamps are immutable evidence and must never be rewritten. The ±5-second tolerance helps diagnose inter-system clock skew but does NOT permit overriding contradictory evidence or manufacturing an artificial event sequence. Ambiguous ordering emits `CLOCK_SKEW_AMBIGUOUS_ORDERING` and caps finding strength at `MODERATE`.
3. **Strict Correlation Hierarchy (No `(reference, amount)` Merging)**: A transaction reference is not globally unique, and separate payments share identical amounts. `(reference, amount)` alone must NEVER establish that records represent the same transaction. Correlation requires verified globally unique identifiers `(session_id, rrn)` or verified institution-scoped references `(institution_id, reference)`. Unverified partial matches are strictly flagged as `CANDIDATE_MATCH` requiring additional corroborating evidence.
4. **Separation of Raw and Normalized Records**: Raw payloads are stored immutably in `raw_records`. Canonical events in `events` point back to their specific `raw_record_id`.
5. **Absence is Not Proof of Non-Occurrence**: A missing receiving bank or switch record indicates `INSUFFICIENT_EVIDENCE` or an evidence gap, not positive proof that an event never happened.
6. **Data-Quality Capping**: Critical data-quality anomalies (e.g. clock drift > 5s, missing primary references) cap finding strength at `MODERATE`.
7. **Pure Determinism**: Identical normalized inputs evaluated against the same rule version produce 100% reproducible, identical findings.
8. **AI Explanation Guardrails**: AI only explains frozen engine findings and must never invent events, IDs, amounts, timestamps, or statuses.
9. **Synthetic Data Only**: All demo scenarios utilize synthetic bank and customer records.
10. **Zero Financial Operations**: No component may move money or trigger financial operations.

---

## 4. Frozen Domain Contracts

### 4.1 The Five Permitted Outcomes (Exact)
1. `DISCREPANCY_CONFIRMED`
2. `LIKELY_ROOT_CAUSE`
3. `INSUFFICIENT_EVIDENCE`
4. `CONFLICTING_EVIDENCE`
5. `NO_DISCREPANCY_FOUND`

### 4.2 The Four Qualitative Finding Strengths (Exact)
1. `HIGH`
2. `MODERATE`
3. `LOW`
4. `INSUFFICIENT`

*(Numerical confidence percentages are strictly prohibited.)*

### 4.3 Shared Analysis Response Contract (10 Required Fields)
`case_id`, `outcome`, `strength`, `rule_version`, `category`, `timeline`, `supporting_event_ids`, `missing_evidence`, `data_quality_flags`, `counterfactuals`.

Additive traceability fields (Part 4): `outcome_reason`, `strength_reason`, `scope_examined`, `supporting_evidence`, `contradicting_evidence`, `alternative_explanations`, `recommended_next_evidence`.

---

## 5. API Routes

| Method | Route | Description | Baseline Status |
|--------|-------|-------------|-----------------|
| `GET` | `/health` | Root service health probe | Operational (`200 OK`) |
| `GET` | `/api/v1/health` | API v1 status and version probe | Operational (`200 OK`) |
| `POST` | `/api/v1/cases` | Register a new disputed case | Implemented (`201`) |
| `GET` | `/api/v1/cases` | List and paginate cases | Implemented (`200`) |
| `GET` | `/api/v1/cases/{id}` | Retrieve comprehensive case details | Implemented (`200`) |
| `POST` | `/api/v1/cases/{id}/evidence` | Ingest synthetic logs for a case | Implemented (`201`) |
| `GET` | `/api/v1/cases/{id}/correlation` | Deterministic event correlation | Implemented (Part 3) |
| `POST` | `/api/v1/cases/{id}/analyze` | Trigger deterministic rules evaluation | Implemented (Part 4) |
| `GET` | `/api/v1/cases/{id}/evidence-pack` | Regenerate read-only evidence pack | Implemented (Part 6) |
| `POST` | `/api/v1/cases/{id}/review` | Record human investigator verdict | Contract Defined (`501`) |

---

## 6. Quickstart Local Development

### Prerequisites
- Python 3.11+
- Node.js v18+ and npm

### Backend Setup & Test
```bash
# 1. Navigate to backend
cd backend

# 2. Copy environment configuration
cp .env.example .env

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run test suite (contracts, health, ingestion, correlation & rules)
python -m pytest

# 5. Start development server
python -m uvicorn app.main:app --reload --port 8000
```
- Interactive API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Probe: [http://localhost:8000/health](http://localhost:8000/health)

### Frontend Setup & Build
```bash
# 1. Navigate to frontend
cd frontend

# 2. Copy environment configuration
cp .env.example .env

# 3. Install dependencies
npm install

# 4. Verify TypeScript build
npm run build

# 5. Start dev server
npm run dev
```
- Frontend Dev URL: [http://localhost:5173](http://localhost:5173)
