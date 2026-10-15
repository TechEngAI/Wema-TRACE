# TRACE Database Migrations

This directory contains repeatable SQL schema migrations compatible with PostgreSQL 14+ and Supabase.

## Migration Files
- `001_initial_schema.sql`: Core schema for `cases`, `source_files`, `raw_records`, `events`, `analyses`, `ai_runs`, and `reviews`.
- `002_case_reviews.sql`: Part 8 append-only human-review records. It uses `case_reviews` rather than the legacy `reviews` table because deterministic analyses are computed on demand and have no persisted `analysis_id`.

> **Part 3 (Correlation) requires no schema migration.** Correlation results are computed deterministically on demand from persisted canonical `events` and their `raw_records` lineage; no persistent correlation tables are created. Source events and raw records are never mutated. Identifier scope is derived from `events.party` (institution source) and global identifiers (`session_id`, `rrn`), so no new columns are needed.
>
> **Part 4 (Rules Engine) requires no schema migration.** Outcomes are computed deterministically on demand from persisted canonical `events` plus the Part 3 correlation result. No `analyses` row is written by `POST /api/v1/cases/{id}/analyze`; the existing `analyses` table remains reserved for a future frozen-snapshot feature. Evaluation is read-only and never mutates source events or raw records.

## Execution Instructions

### Option 1: Supabase Dashboard
1. Log in to your Supabase project dashboard.
2. Navigate to **SQL Editor**.
3. Create a **New query**.
4. Paste the entire content of `001_initial_schema.sql` and click **Run**.

### Option 2: psql Command Line
```bash
psql "postgresql://postgres:[YOUR-PASSWORD]@[YOUR-HOST]:5432/postgres" -f 001_initial_schema.sql
```

### Option 3: Local SQLite for Testing
For unit tests, TRACE leverages SQLAlchemy's metadata engine to automatically initialize schemas in an isolated in-memory or temporary SQLite database (`sqlite:///:memory:`). No Docker daemon or live Supabase account is required to execute the test suite.
