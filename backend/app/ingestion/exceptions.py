"""Ingestion Domain Exceptions.

These exceptions map to safe, client-actionable HTTP errors without
leaking stack traces, internal paths, or database internals.
"""

from typing import Any, Dict, Optional
from uuid import UUID


class IngestionError(Exception):
    """Base class for evidence ingestion errors."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class FileTooLargeError(IngestionError):
    """Uploaded file exceeds configured byte limit."""

    def __init__(self, size_bytes: int, max_bytes: int):
        super().__init__(
            message=f"Uploaded file size ({size_bytes} bytes) exceeds maximum allowable limit of {max_bytes} bytes.",
            status_code=413,
            details={"size_bytes": size_bytes, "max_bytes": max_bytes},
        )


class RowLimitExceededError(IngestionError):
    """Record count exceeds configured batch limit."""

    def __init__(self, count: int, max_rows: int):
        super().__init__(
            message=f"Uploaded record count ({count}) exceeds maximum allowable limit of {max_rows} rows.",
            status_code=400,
            details={"record_count": count, "max_rows": max_rows},
        )


class UnsupportedFormatError(IngestionError):
    """File extension or content format is not supported."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=400, details=details)


class DuplicateEvidenceError(IngestionError):
    """Exact identical file hash already ingested for this case."""

    def __init__(self, case_id: UUID, sha256_hash: str, existing_source_file_id: UUID):
        super().__init__(
            message=f"Duplicate evidence detected. File with SHA-256 hash '{sha256_hash}' was already ingested for this case.",
            status_code=409,
            details={
                "case_id": str(case_id),
                "sha256_hash": sha256_hash,
                "existing_source_file_id": str(existing_source_file_id),
            },
        )


class CaseNotFoundError(IngestionError):
    """Target case does not exist."""

    def __init__(self, case_id: UUID):
        super().__init__(
            message=f"Case with ID '{case_id}' was not found.",
            status_code=404,
            details={"case_id": str(case_id)},
        )


class DataValidationError(IngestionError):
    """Row or payload failed contract validation."""

    def __init__(self, message: str, row_index: Optional[int] = None, field: Optional[str] = None):
        details = {}
        if row_index is not None:
            details["row_index"] = row_index
        if field is not None:
            details["field"] = field
        super().__init__(message=message, status_code=400, details=details)
