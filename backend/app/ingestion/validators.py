"""Evidence Ingestion Validators.

Enforces:
1. Strict integer minor units for currency (₦85,000 = 8500000 kobo; no floats).
2. ISO 8601 timestamps with explicit timezone offsets (no timezone-naive inference).
3. Whitelisted currency codes.
4. Mandatory identification references per institution category.
"""

from datetime import datetime
import hashlib
from typing import Any, Dict, List, Optional
import uuid

from app.config import settings
from app.ingestion.exceptions import (
    DataValidationError,
    FileTooLargeError,
    RowLimitExceededError,
)
from app.schemas.enums import (
    EventActionEnum,
    EventStatusEnum,
    InstitutionPartyEnum,
)

# Source-specific mandatory fields
SOURCE_REQUIRED_FIELDS: Dict[InstitutionPartyEnum, List[str]] = {
    InstitutionPartyEnum.ORIGINATING_BANK: [
        "timestamp",
        "reference",
        "amount",
        "currency",
        "status",
    ],
    InstitutionPartyEnum.SWITCH: [
        "timestamp",
        "session_id",
        "action",
        "status",
        "amount",
        "currency",
    ],
    InstitutionPartyEnum.RECEIVING_BANK: [
        "timestamp",
        "reference",
        "amount",
        "currency",
        "status",
    ],
    InstitutionPartyEnum.MERCHANT: [
        "timestamp",
        "order_id",
        "reference",
        "amount",
        "currency",
        "status",
    ],
}


def compute_sha256(content_bytes: bytes) -> str:
    """Compute hex SHA-256 digest of file bytes."""
    return hashlib.sha256(content_bytes).hexdigest()


def validate_file_size(content_bytes: bytes) -> None:
    """Validate raw file byte length against configured ceiling."""
    size = len(content_bytes)
    if size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise FileTooLargeError(size, settings.MAX_UPLOAD_SIZE_BYTES)


def validate_row_count(records: List[Dict[str, Any]]) -> None:
    """Validate record count against configured ceiling."""
    count = len(records)
    if count > settings.MAX_UPLOAD_ROWS:
        raise RowLimitExceededError(count, settings.MAX_UPLOAD_ROWS)


def validate_timestamp(val: Any, row_idx: int) -> datetime:
    """Validate timestamp string is ISO 8601 with explicit timezone offset.

    CRITICAL RULE: Timezone-naive timestamps are strictly rejected.
    """
    if not val or not isinstance(val, str) or not val.strip():
        raise DataValidationError(
            "Timestamp is required and cannot be empty.",
            row_index=row_idx,
            field="timestamp",
        )

    val_str = val.strip()

    try:
        # datetime.fromisoformat parses ISO 8601 formats including Z and +/-HH:MM
        # Python 3.11+ natively handles 'Z' and arbitrary offsets
        dt = datetime.fromisoformat(val_str)
    except Exception as err:
        raise DataValidationError(
            f"Invalid ISO 8601 timestamp '{val_str}': {str(err)}",
            row_index=row_idx,
            field="timestamp",
        )

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise DataValidationError(
            f"Timezone offset missing in timestamp '{val_str}'. Timestamps must include an explicit offset (e.g. '+01:00' or 'Z'). Silent UTC inference is forbidden.",
            row_index=row_idx,
            field="timestamp",
        )

    return dt


def validate_amount_minor_units(val: Any, row_idx: int) -> int:
    """Validate monetary amount is a strictly positive integer in minor units (kobo).

    CRITICAL RULE: Floating-point amounts are strictly prohibited.
    """
    if val is None or val == "":
        raise DataValidationError(
            "Amount is required and cannot be empty.",
            row_index=row_idx,
            field="amount",
        )

    # Check if a Python float was passed in JSON
    if isinstance(val, float):
        raise DataValidationError(
            f"Floating-point amount '{val}' is prohibited. Monetary values must be integer minor units (e.g., 8500000 kobo for ₦85,000).",
            row_index=row_idx,
            field="amount",
        )

    # If passed as string, ensure no decimal point exists
    val_str = str(val).strip()
    if "." in val_str:
        raise DataValidationError(
            f"Floating-point representation '{val_str}' is prohibited. Monetary values must be integer minor units (e.g., 8500000 kobo).",
            row_index=row_idx,
            field="amount",
        )

    try:
        amount_int = int(val_str)
    except ValueError:
        raise DataValidationError(
            f"Invalid non-integer amount '{val_str}'. Expected integer minor units.",
            row_index=row_idx,
            field="amount",
        )

    if amount_int <= 0:
        raise DataValidationError(
            f"Amount must be strictly positive (got {amount_int}). Zero or negative transactions are invalid.",
            row_index=row_idx,
            field="amount",
        )

    return amount_int


def validate_currency(val: Any, row_idx: int) -> str:
    """Validate currency is an approved 3-letter ISO code."""
    if not val or not isinstance(val, str) or not val.strip():
        raise DataValidationError(
            "Currency code is required.",
            row_index=row_idx,
            field="currency",
        )

    curr = val.strip().upper()
    if curr not in settings.SUPPORTED_CURRENCIES:
        raise DataValidationError(
            f"Unsupported currency '{curr}'. Supported currencies are: {', '.join(settings.SUPPORTED_CURRENCIES)}.",
            row_index=row_idx,
            field="currency",
        )

    return curr


def validate_action(val: Any, institution: InstitutionPartyEnum, row_idx: int) -> EventActionEnum:
    """Validate or assign canonical action for the institution."""
    if val is None or str(val).strip() == "":
        # Default action based on institution
        if institution == InstitutionPartyEnum.ORIGINATING_BANK:
            return EventActionEnum.DEBIT
        elif institution == InstitutionPartyEnum.RECEIVING_BANK:
            return EventActionEnum.CREDIT
        elif institution == InstitutionPartyEnum.MERCHANT:
            return EventActionEnum.ACK
        else:
            raise DataValidationError(
                f"Action is mandatory for institution source '{institution.value}'.",
                row_index=row_idx,
                field="action",
            )

    action_str = str(val).strip().upper()
    try:
        return EventActionEnum(action_str)
    except ValueError:
        valid_actions = [e.value for e in EventActionEnum]
        raise DataValidationError(
            f"Unsupported action '{action_str}'. Must be one of: {', '.join(valid_actions)}.",
            row_index=row_idx,
            field="action",
        )


def validate_status(val: Any, row_idx: int) -> EventStatusEnum:
    """Validate transaction status matches permitted EventStatusEnum."""
    if not val or not str(val).strip():
        raise DataValidationError(
            "Transaction status is required.",
            row_index=row_idx,
            field="status",
        )

    status_str = str(val).strip().upper()
    try:
        return EventStatusEnum(status_str)
    except ValueError:
        valid_statuses = [s.value for s in EventStatusEnum]
        raise DataValidationError(
            f"Invalid transaction status '{status_str}'. Must be one of: {', '.join(valid_statuses)}.",
            row_index=row_idx,
            field="status",
        )


def validate_source_records(
    records: List[Dict[str, Any]],
    institution: InstitutionPartyEnum,
) -> None:
    """Validate that every record in the batch satisfies the institution's contract."""
    required_fields = SOURCE_REQUIRED_FIELDS.get(institution, [])
    row_idx = 0

    for record in records:
        row_idx += 1

        # Check required fields presence
        for req in required_fields:
            if req not in record or record[req] is None or str(record[req]).strip() == "":
                raise DataValidationError(
                    f"Missing required field '{req}' for institution category '{institution.value}'.",
                    row_index=row_idx,
                    field=req,
                )

        # Validate core types
        validate_timestamp(record["timestamp"], row_idx)
        validate_amount_minor_units(record["amount"], row_idx)
        validate_currency(record["currency"], row_idx)
        validate_status(record["status"], row_idx)
        if "action" in record and record["action"]:
            validate_action(record["action"], institution, row_idx)
