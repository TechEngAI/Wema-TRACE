"""SQLAlchemy ORM Models for TRACE Persistence.

Strictly follows the data model specification in /docs/architecture.md.
Portable across SQLite (for zero-dependency unit tests) and PostgreSQL / Supabase.
"""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TZDateTime(TypeDecorator):
    """Platform-independent DateTime with timezone.

    In PostgreSQL, uses native TIMESTAMP WITH TIME ZONE.
    In SQLite, stores ISO-formatted string with explicit offset and parses back
    to an offset-aware datetime, ensuring zero timezone degradation.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(50))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, datetime):
                return value.isoformat() if dialect.name == "sqlite" else value
            elif isinstance(value, str):
                return value
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            return value
        return value


def utc_now() -> datetime:
    """Return current UTC timestamp with explicit timezone."""
    return datetime.now(timezone.utc)


class CaseModel(Base):
    """Dispute investigation case."""

    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    reference: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    initiating_party: Mapped[str] = mapped_column(String(50), nullable=False)
    counterparty: Mapped[str] = mapped_column(String(50), nullable=False)
    disputed_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    rrn: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    source_files: Mapped[List["SourceFileModel"]] = relationship(
        "SourceFileModel",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    raw_records: Mapped[List["RawRecordModel"]] = relationship(
        "RawRecordModel",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    events: Mapped[List["EventModel"]] = relationship(
        "EventModel",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[List["CaseReviewModel"]] = relationship(
        "CaseReviewModel", back_populates="case", cascade="all, delete-orphan"
    )


class SourceFileModel(Base):
    """Uploaded synthetic evidence log file metadata."""

    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("case_id", "sha256_hash", name="uq_case_source_file_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CSV or JSONL
    institution_source: Mapped[str] = mapped_column(String(50), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utc_now,
        nullable=False,
    )

    case: Mapped["CaseModel"] = relationship("CaseModel", back_populates="source_files")
    raw_records: Mapped[List["RawRecordModel"]] = relationship(
        "RawRecordModel",
        back_populates="source_file",
        cascade="all, delete-orphan",
    )


class RawRecordModel(Base):
    """Immutable parsed raw row/object from uploaded source file."""

    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint("source_file_id", "record_index", name="uq_source_file_record_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    record_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utc_now,
        nullable=False,
    )

    case: Mapped["CaseModel"] = relationship("CaseModel", back_populates="raw_records")
    source_file: Mapped["SourceFileModel"] = relationship(
        "SourceFileModel", back_populates="raw_records"
    )
    event: Mapped[Optional["EventModel"]] = relationship(
        "EventModel",
        back_populates="raw_record",
        uselist=False,
        cascade="all, delete-orphan",
    )


class EventModel(Base):
    """Canonical normalized financial event synthesized from a raw record."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    raw_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_records.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    party: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    rrn: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
    )
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=utc_now,
        nullable=False,
    )

    case: Mapped["CaseModel"] = relationship("CaseModel", back_populates="events")
    raw_record: Mapped["RawRecordModel"] = relationship("RawRecordModel", back_populates="event")


class CaseReviewModel(Base):
    """Append-only human assessment plus a server-generated finding snapshot."""

    __tablename__ = "case_reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reviewer_display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_additional_evidence: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    finding_reference: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    machine_finding_snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utc_now, nullable=False)

    case: Mapped["CaseModel"] = relationship("CaseModel", back_populates="reviews")
