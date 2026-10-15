"""Correlation Exceptions."""

from typing import Any, Dict, Optional
from uuid import UUID


class CorrelationError(Exception):
    """Base class for transaction correlation errors."""

    def __init__(self, message: str, status_code: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CrossCaseCorrelationError(CorrelationError):
    """Attempted to correlate events belonging to different cases."""

    def __init__(self, case_ids: list):
        super().__init__(
            message=f"Cross-case correlation prohibited. Attempted to correlate events across multiple cases: {case_ids}.",
            status_code=400,
            details={"case_ids": [str(c) for c in case_ids]},
        )
