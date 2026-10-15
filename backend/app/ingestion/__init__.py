"""TRACE Evidence Ingestion & Normalization Package."""

from app.ingestion.exceptions import (
    CaseNotFoundError,
    DataValidationError,
    DuplicateEvidenceError,
    FileTooLargeError,
    IngestionError,
    RowLimitExceededError,
    UnsupportedFormatError,
)
from app.ingestion.normalizer import normalize_record
from app.ingestion.parsers import parse_csv, parse_evidence_file, parse_jsonl
from app.ingestion.service import ingest_evidence
from app.ingestion.validators import (
    compute_sha256,
    validate_amount_minor_units,
    validate_currency,
    validate_file_size,
    validate_row_count,
    validate_source_records,
    validate_timestamp,
)

__all__ = [
    "IngestionError",
    "FileTooLargeError",
    "RowLimitExceededError",
    "UnsupportedFormatError",
    "DuplicateEvidenceError",
    "CaseNotFoundError",
    "DataValidationError",
    "parse_csv",
    "parse_jsonl",
    "parse_evidence_file",
    "validate_file_size",
    "validate_row_count",
    "validate_timestamp",
    "validate_amount_minor_units",
    "validate_currency",
    "validate_source_records",
    "compute_sha256",
    "normalize_record",
    "ingest_evidence",
]
