-- ==============================================================================
-- TRACE PostgreSQL Schema Migration: 001_initial_schema.sql
-- Target: PostgreSQL 14+ / Supabase
-- Description: Core schema for cases, source files, raw records, canonical events,
--              analyses, AI runs, and human reviews.
-- ==============================================================================

-- Enable UUID extension if not already present
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Cases Table
CREATE TABLE IF NOT EXISTS cases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reference VARCHAR(100) NOT NULL UNIQUE,
    initiating_party VARCHAR(50) NOT NULL,
    counterparty VARCHAR(50) NOT NULL,
    disputed_amount BIGINT NOT NULL CHECK (disputed_amount > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
    status VARCHAR(50) NOT NULL DEFAULT 'OPEN',
    session_id VARCHAR(100),
    rrn VARCHAR(100),
    narrative TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cases_reference ON cases(reference);
CREATE INDEX IF NOT EXISTS idx_cases_session_id ON cases(session_id);
CREATE INDEX IF NOT EXISTS idx_cases_rrn ON cases(rrn);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

-- 2. Source Files Table (Evidence Log Uploads)
CREATE TABLE IF NOT EXISTS source_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(20) NOT NULL, -- 'CSV' or 'JSONL'
    institution_source VARCHAR(50) NOT NULL, -- 'ORIGINATING_BANK', 'SWITCH', 'RECEIVING_BANK', 'MERCHANT'
    sha256_hash VARCHAR(64) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    uploaded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_case_source_file_hash UNIQUE (case_id, sha256_hash)
);

CREATE INDEX IF NOT EXISTS idx_source_files_case_id ON source_files(case_id);
CREATE INDEX IF NOT EXISTS idx_source_files_hash ON source_files(sha256_hash);

-- 3. Raw Records Table (Immutable Source Rows / Payloads)
CREATE TABLE IF NOT EXISTS raw_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    source_file_id UUID NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    record_index INTEGER NOT NULL,
    raw_payload JSONB NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_file_record_index UNIQUE (source_file_id, record_index)
);

CREATE INDEX IF NOT EXISTS idx_raw_records_case_id ON raw_records(case_id);
CREATE INDEX IF NOT EXISTS idx_raw_records_source_file_id ON raw_records(source_file_id);

-- 4. Canonical Events Table (Normalized Financial Events)
CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    raw_record_id UUID NOT NULL UNIQUE REFERENCES raw_records(id) ON DELETE CASCADE,
    party VARCHAR(50) NOT NULL,
    action VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    amount_minor_units BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
    session_id VARCHAR(100),
    rrn VARCHAR(100),
    reference VARCHAR(100),
    channel VARCHAR(50),
    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_case_id ON events(case_id);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_rrn ON events(rrn);
CREATE INDEX IF NOT EXISTS idx_events_reference ON events(reference);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(event_timestamp);

-- 5. Analyses Table (Deterministic Rules Engine Findings - Part 3)
CREATE TABLE IF NOT EXISTS analyses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    outcome VARCHAR(50) NOT NULL CHECK (
        outcome IN (
            'DISCREPANCY_CONFIRMED',
            'LIKELY_ROOT_CAUSE',
            'INSUFFICIENT_EVIDENCE',
            'CONFLICTING_EVIDENCE',
            'NO_DISCREPANCY_FOUND'
        )
    ),
    strength VARCHAR(20) NOT NULL CHECK (
        strength IN ('HIGH', 'MODERATE', 'LOW', 'INSUFFICIENT')
    ),
    rule_version VARCHAR(50) NOT NULL,
    category VARCHAR(100) NOT NULL,
    timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
    supporting_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    counterfactuals JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_case_id ON analyses(case_id);

-- 6. AI Runs Table (Explanations Only - Part 4)
CREATE TABLE IF NOT EXISTS ai_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    analysis_id UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    model_name VARCHAR(100) NOT NULL,
    explanation TEXT NOT NULL,
    prompt_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_explain_only BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_analysis_id ON ai_runs(analysis_id);

-- 7. Reviews Table (Human Investigator Sign-off - Part 4)
CREATE TABLE IF NOT EXISTS reviews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    analysis_id UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    reviewer_id VARCHAR(100) NOT NULL,
    verdict VARCHAR(50) NOT NULL CHECK (
        verdict IN ('CONCUR', 'OVERRULE', 'REQUEST_FURTHER_EVIDENCE')
    ),
    notes TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reviews_case_id ON reviews(case_id);
