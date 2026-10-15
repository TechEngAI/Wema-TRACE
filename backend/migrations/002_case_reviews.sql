-- Part 8: append-only human review records with server-calculated finding snapshots.
-- This table deliberately does not use the legacy `reviews.analysis_id` design because
-- TRACE analyses are computed on demand and have no persisted analysis ID.
CREATE TABLE IF NOT EXISTS case_reviews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    reviewer_display_name VARCHAR(100) NOT NULL,
    decision VARCHAR(50) NOT NULL CHECK (decision IN ('ACCEPT_FINDING', 'DISPUTE_FINDING', 'REQUEST_MORE_EVIDENCE')),
    rationale TEXT NOT NULL,
    notes TEXT,
    requested_additional_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    finding_reference VARCHAR(64) NOT NULL,
    machine_finding_snapshot JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_case_reviews_case_created ON case_reviews(case_id, created_at);
