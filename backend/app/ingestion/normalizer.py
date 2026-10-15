"""Evidence Normalizer.

Transforms validated raw records into canonical EventModel instances.
Maintains strict 1:1 lineage to raw_record_id.
Does NOT perform correlation, cross-matching, or rules determination.
"""

from typing import Any, Dict
import uuid

from app.db.models import EventModel
from app.ingestion.validators import (
    validate_action,
    validate_amount_minor_units,
    validate_currency,
    validate_status,
    validate_timestamp,
)
from app.schemas.enums import InstitutionPartyEnum


def normalize_record(
    raw_record_id: uuid.UUID,
    case_id: uuid.UUID,
    raw_payload: Dict[str, Any],
    institution: InstitutionPartyEnum,
    row_idx: int,
) -> EventModel:
    """Transform an individual raw record dictionary into a canonical EventModel."""
    # 1. Parse core fields
    event_timestamp = validate_timestamp(raw_payload.get("timestamp"), row_idx)
    amount_minor_units = validate_amount_minor_units(raw_payload.get("amount"), row_idx)
    currency = validate_currency(raw_payload.get("currency"), row_idx)
    status_enum = validate_status(raw_payload.get("status"), row_idx)
    action_enum = validate_action(raw_payload.get("action"), institution, row_idx)

    # 2. Extract correlation identifiers
    session_id = raw_payload.get("session_id")
    if session_id is not None:
        session_id = str(session_id).strip() or None

    rrn = raw_payload.get("rrn")
    if rrn is not None:
        rrn = str(rrn).strip() or None

    # Reference resolution based on institution category
    reference = raw_payload.get("reference")
    if not reference and institution == InstitutionPartyEnum.MERCHANT:
        reference = raw_payload.get("order_id")

    if reference is not None:
        reference = str(reference).strip() or None

    channel = raw_payload.get("channel")
    if channel is not None:
        channel = str(channel).strip() or None

    # 3. Preserve source-specific non-canonical fields in metadata_json
    known_keys = {
        "timestamp",
        "amount",
        "currency",
        "status",
        "action",
        "session_id",
        "rrn",
        "reference",
        "channel",
    }
    extra_metadata = {k: v for k, v in raw_payload.items() if k not in known_keys}

    # 4. Construct canonical EventModel with immutable lineage link
    return EventModel(
        id=uuid.uuid4(),
        case_id=case_id,
        raw_record_id=raw_record_id,
        party=institution.value,
        action=action_enum.value,
        status=status_enum.value,
        amount_minor_units=amount_minor_units,
        currency=currency,
        session_id=session_id,
        rrn=rrn,
        reference=reference,
        channel=channel,
        event_timestamp=event_timestamp,
        metadata_json=extra_metadata,
    )
