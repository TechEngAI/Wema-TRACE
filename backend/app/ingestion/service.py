"""Evidence Ingestion Service.

Coordinates file parsing, validation, provenance preservation, and canonical normalization.
Maintains strict transaction atomicity: any validation or database error triggers a full rollback.
"""

from typing import Optional
import uuid
from sqlalchemy.orm import Session

from app.db.models import CaseModel, RawRecordModel, SourceFileModel
from app.ingestion.exceptions import (
    CaseNotFoundError,
    DuplicateEvidenceError,
)
from app.ingestion.normalizer import normalize_record
from app.ingestion.parsers import parse_evidence_file
from app.ingestion.validators import (
    compute_sha256,
    validate_file_size,
    validate_row_count,
    validate_source_records,
)
from app.schemas.enums import InstitutionPartyEnum
from app.schemas.evidence import EvidenceUploadResponse


def ingest_evidence(
    db: Session,
    case_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    institution_source: InstitutionPartyEnum,
    file_type_hint: Optional[str] = None,
) -> EvidenceUploadResponse:
    """Ingest, validate, and normalize an evidence file for a target dispute case.

    Guarantees:
    - Atomicity: File metadata, raw records, and normalized events commit together or not at all.
    - Immutability: Raw records preserve source payloads without modification.
    - Provenance: Each normalized event links directly to its source raw record.
    """
    # 1. Verify case existence
    case = db.query(CaseModel).filter(CaseModel.id == case_id).first()
    if not case:
        raise CaseNotFoundError(case_id)

    # 2. Check file size limits
    validate_file_size(file_bytes)

    # 3. Compute SHA-256 hash and check duplicate evidence
    sha256_hash = compute_sha256(file_bytes)
    existing_file = (
        db.query(SourceFileModel)
        .filter(
            SourceFileModel.case_id == case_id,
            SourceFileModel.sha256_hash == sha256_hash,
        )
        .first()
    )
    if existing_file:
        raise DuplicateEvidenceError(
            case_id=case_id,
            sha256_hash=sha256_hash,
            existing_source_file_id=existing_file.id,
        )

    # 4. Parse file structure (CSV or JSONL)
    detected_type, records = parse_evidence_file(file_bytes, filename, file_type_hint)

    # 5. Validate row counts
    validate_row_count(records)

    # 6. Validate records against institution contract
    validate_source_records(records, institution_source)

    # 7. Persist transactionally
    source_file_id = uuid.uuid4()
    source_file = SourceFileModel(
        id=source_file_id,
        case_id=case_id,
        filename=filename,
        file_type=detected_type,
        institution_source=institution_source.value,
        sha256_hash=sha256_hash,
        file_size_bytes=len(file_bytes),
        record_count=len(records),
    )

    try:
        db.add(source_file)

        raw_models = []
        event_models = []

        for idx, rec in enumerate(records):
            raw_rec_id = uuid.uuid4()
            raw_rec = RawRecordModel(
                id=raw_rec_id,
                case_id=case_id,
                source_file_id=source_file_id,
                record_index=idx,
                raw_payload=rec,
            )
            raw_models.append(raw_rec)

            event = normalize_record(
                raw_record_id=raw_rec_id,
                case_id=case_id,
                raw_payload=rec,
                institution=institution_source,
                row_idx=idx + 1,
            )
            event_models.append(event)

        db.add_all(raw_models)
        db.add_all(event_models)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return EvidenceUploadResponse(
        source_file_id=source_file_id,
        case_id=case_id,
        filename=filename,
        records_ingested=len(records),
        sha256_hash=sha256_hash,
        status="INGESTED",
    )
