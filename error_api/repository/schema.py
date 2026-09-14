"""Independent SQLAlchemy metadata for the observability schema.

This metadata must never be imported into the bot repository's Alembic stream.
Views, grants, and the atomic recording routine are migration-owned objects.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    desc,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

OBSERVABILITY_SCHEMA = "observability"


class ErrorApiBase(DeclarativeBase):
    """Declarative base owned exclusively by the Error API migrations."""


class ErrorGroupRecord(ErrorApiBase):
    __tablename__ = "error_groups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'investigating', 'fixing', 'monitoring', "
            "'resolved', 'ignored')",
            name="ck_error_groups_status",
        ),
        CheckConstraint("occurrence_count >= 1", name="ck_error_groups_count_positive"),
        {"schema": OBSERVABILITY_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    event_code: Mapped[str] = mapped_column(String(128), nullable=False)
    exception_type: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="new"
    )
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )
    first_release: Mapped[str | None] = mapped_column(String(128))
    last_release: Mapped[str | None] = mapped_column(String(128))
    linked_change: Mapped[str | None] = mapped_column(String(500))
    fixed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ErrorOccurrenceRecord(ErrorApiBase):
    __tablename__ = "error_occurrences"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('ERROR', 'CRITICAL')",
            name="ck_error_occurrences_severity",
        ),
        Index("ix_error_occurrences_occurred_at", desc("occurred_at")),
        Index(
            "ix_error_occurrences_group_occurred",
            "error_group_id",
            desc("occurred_at"),
        ),
        {"schema": OBSERVABILITY_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    error_group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{OBSERVABILITY_SCHEMA}.error_groups.id"),
        nullable=False,
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    logger_name: Mapped[str] = mapped_column(String(128), nullable=False)
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    exception_type: Mapped[str | None] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(String(4000), nullable=False)
    traceback: Mapped[str | None] = mapped_column(String(32000))
    provider: Mapped[str | None] = mapped_column(String(128))
    media_kind: Mapped[str | None] = mapped_column(String(128))
    release: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ErrorRequestLinkRecord(ErrorApiBase):
    __tablename__ = "error_request_links"
    __table_args__ = ({"schema": OBSERVABILITY_SCHEMA},)

    error_occurrence_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{OBSERVABILITY_SCHEMA}.error_occurrences.id"),
        primary_key=True,
    )
    # Deliberately no cross-schema FK: missing application requests must not
    # prevent best-effort evidence recording.
    media_request_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class ErrorNoteRecord(ErrorApiBase):
    __tablename__ = "error_notes"
    __table_args__ = (
        CheckConstraint(
            "length(note) BETWEEN 1 AND 4000",
            name="ck_error_notes_note_length",
        ),
        Index(
            "ix_error_notes_group_created",
            "error_group_id",
            desc("created_at"),
        ),
        {"schema": OBSERVABILITY_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    error_group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{OBSERVABILITY_SCHEMA}.error_groups.id"),
        nullable=False,
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
