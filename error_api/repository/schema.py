"""SQLAlchemy metadata owned by the Error API."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData(schema="observability")

error_groups = Table(
    "error_groups",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("fingerprint", String(128), nullable=False),
    Column("display_name", String(256), nullable=False),
    Column("event_code", String(128), nullable=False),
    Column("exception_type", String(256)),
    Column("status", String(16), nullable=False, server_default="new"),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("occurrence_count", BigInteger, nullable=False),
    Column("first_release", String(128)),
    Column("last_release", String(128)),
    Column("linked_change", String(512)),
    Column("fixed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('new', 'investigating', 'fixing', 'monitoring', "
        "'resolved', 'ignored')",
        name="ck_error_groups_status",
    ),
    CheckConstraint("occurrence_count >= 1", name="ck_error_groups_occurrence_count"),
    UniqueConstraint("fingerprint", name="uq_error_groups_fingerprint"),
)

error_occurrences = Table(
    "error_occurrences",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "error_group_id",
        BigInteger,
        ForeignKey("observability.error_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("logger_name", String(255), nullable=False),
    Column("component", String(255)),
    Column("exception_type", String(256)),
    Column("message", Text, nullable=False),
    Column("traceback", Text),
    Column("provider", String(64)),
    Column("media_kind", String(64)),
    Column("release", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "severity IN ('ERROR', 'CRITICAL')",
        name="ck_error_occurrences_severity",
    ),
)

error_request_links = Table(
    "error_request_links",
    metadata,
    Column(
        "error_occurrence_id",
        BigInteger,
        ForeignKey("observability.error_occurrences.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("media_request_id", BigInteger, primary_key=True),
    CheckConstraint(
        "media_request_id > 0",
        name="ck_error_request_links_request_id",
    ),
)

error_notes = Table(
    "error_notes",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "error_group_id",
        BigInteger,
        ForeignKey("observability.error_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("note", Text, nullable=False),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "char_length(note) BETWEEN 1 AND 4000",
        name="ck_error_notes_note_length",
    ),
)

Index(
    "ix_error_occurrences_group_occurred_at",
    error_occurrences.c.error_group_id,
    error_occurrences.c.occurred_at.desc(),
)
Index(
    "ix_error_occurrences_occurred_at",
    error_occurrences.c.occurred_at.desc(),
)
Index(
    "ix_error_request_links_media_request_id",
    error_request_links.c.media_request_id,
)
Index(
    "ix_error_notes_group_created_at",
    error_notes.c.error_group_id,
    error_notes.c.created_at.desc(),
)
