"""create observability ledger

Revision ID: 20260918_0001
Revises:
Create Date: 2026-09-18 00:00:00.000000
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from error_api.migrations.runtime_privileges import (
    apply_runtime_privileges,
    validate_runtime_roles,
)

revision: str = "20260918_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "observability"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        msg = "The observability ledger requires PostgreSQL"
        raise RuntimeError(msg)
    if not op.get_context().as_sql and (
        bind.execute(sa.text("SELECT to_regclass('public.media_requests')")).scalar()
        is None
    ):
        msg = "Main application migrations must create public.media_requests first"
        raise RuntimeError(msg)

    op.create_table(
        "error_groups",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("event_code", sa.String(128), nullable=False),
        sa.Column("exception_type", sa.String(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.BigInteger(), nullable=False),
        sa.Column("first_release", sa.String(128), nullable=True),
        sa.Column("last_release", sa.String(128), nullable=True),
        sa.Column("linked_change", sa.String(512), nullable=True),
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'investigating', 'fixing', 'monitoring', "
            "'resolved', 'ignored')",
            name="ck_error_groups_status",
        ),
        sa.CheckConstraint(
            "occurrence_count >= 1",
            name="ck_error_groups_occurrence_count",
        ),
        sa.UniqueConstraint("fingerprint", name="uq_error_groups_fingerprint"),
        schema=SCHEMA,
    )
    op.create_table(
        "error_occurrences",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("error_group_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("logger_name", sa.String(255), nullable=False),
        sa.Column("component", sa.String(255), nullable=True),
        sa.Column("exception_type", sa.String(256), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("media_kind", sa.String(64), nullable=True),
        sa.Column("release", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('ERROR', 'CRITICAL')",
            name="ck_error_occurrences_severity",
        ),
        sa.ForeignKeyConstraint(
            ["error_group_id"],
            [f"{SCHEMA}.error_groups.id"],
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_error_occurrences_group_occurred_at",
        "error_occurrences",
        ["error_group_id", sa.text("occurred_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_error_occurrences_occurred_at",
        "error_occurrences",
        [sa.text("occurred_at DESC")],
        schema=SCHEMA,
    )
    op.create_table(
        "error_request_links",
        sa.Column("error_occurrence_id", sa.BigInteger(), nullable=False),
        sa.Column("media_request_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "media_request_id > 0",
            name="ck_error_request_links_request_id",
        ),
        sa.ForeignKeyConstraint(
            ["error_occurrence_id"],
            [f"{SCHEMA}.error_occurrences.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "error_occurrence_id",
            "media_request_id",
            name="pk_error_request_links",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_error_request_links_media_request_id",
        "error_request_links",
        ["media_request_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "error_notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("error_group_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(note) BETWEEN 1 AND 4000",
            name="ck_error_notes_note_length",
        ),
        sa.ForeignKeyConstraint(
            ["error_group_id"],
            [f"{SCHEMA}.error_groups.id"],
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_error_notes_group_created_at",
        "error_notes",
        ["error_group_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )

    op.execute(
        sa.text(
            """
CREATE FUNCTION observability.touch_error_group()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, observability
AS $$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TRIGGER trg_error_groups_updated_at
BEFORE UPDATE ON observability.error_groups
FOR EACH ROW EXECUTE FUNCTION observability.touch_error_group()
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE FUNCTION observability.record_error(
    p_fingerprint text,
    p_display_name text,
    p_event_code text,
    p_occurred_at timestamptz,
    p_severity text,
    p_logger_name text,
    p_component text,
    p_exception_type text,
    p_message text,
    p_traceback text,
    p_provider text,
    p_media_kind text,
    p_release text,
    p_media_request_ids bigint[] DEFAULT ARRAY[]::bigint[]
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, observability
AS $$
DECLARE
    v_error_group_id bigint;
    v_occurrence_id bigint;
BEGIN
    IF p_fingerprint IS NULL OR char_length(p_fingerprint) NOT BETWEEN 1 AND 128
       OR p_display_name IS NULL OR char_length(p_display_name) NOT BETWEEN 1 AND 256
       OR p_event_code IS NULL OR char_length(p_event_code) NOT BETWEEN 1 AND 128
       OR p_occurred_at IS NULL
       OR p_severity NOT IN ('ERROR', 'CRITICAL')
       OR p_logger_name IS NULL OR char_length(p_logger_name) NOT BETWEEN 1 AND 255
       OR p_component IS NOT NULL AND char_length(p_component) > 255
       OR p_exception_type IS NOT NULL AND char_length(p_exception_type) > 256
       OR p_message IS NULL OR char_length(p_message) NOT BETWEEN 1 AND 8192
       OR p_traceback IS NOT NULL AND char_length(p_traceback) > 65536
       OR p_provider IS NOT NULL AND char_length(p_provider) > 64
       OR p_media_kind IS NOT NULL AND char_length(p_media_kind) > 64
       OR p_release IS NOT NULL AND char_length(p_release) > 128
       OR cardinality(p_media_request_ids) > 100
       OR EXISTS (
           SELECT 1
           FROM unnest(COALESCE(p_media_request_ids, ARRAY[]::bigint[])) AS request_id
           WHERE request_id IS NULL OR request_id <= 0
       )
    THEN
        RAISE EXCEPTION 'invalid bounded error event';
    END IF;

    INSERT INTO observability.error_groups (
        fingerprint, display_name, event_code, exception_type, status,
        first_seen_at, last_seen_at, occurrence_count, first_release,
        last_release, created_at, updated_at
    )
    VALUES (
        p_fingerprint, p_display_name, p_event_code, p_exception_type, 'new',
        p_occurred_at, p_occurred_at, 1, p_release, p_release,
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    )
    ON CONFLICT (fingerprint) DO UPDATE
    SET
        first_seen_at = LEAST(
            observability.error_groups.first_seen_at,
            EXCLUDED.first_seen_at
        ),
        last_seen_at = GREATEST(
            observability.error_groups.last_seen_at,
            EXCLUDED.last_seen_at
        ),
        occurrence_count = observability.error_groups.occurrence_count + 1,
        first_release = CASE
            WHEN EXCLUDED.first_seen_at < observability.error_groups.first_seen_at
            THEN EXCLUDED.first_release
            ELSE observability.error_groups.first_release
        END,
        last_release = CASE
            WHEN EXCLUDED.last_seen_at >= observability.error_groups.last_seen_at
            THEN COALESCE(EXCLUDED.last_release, observability.error_groups.last_release)
            ELSE observability.error_groups.last_release
        END
    RETURNING id INTO v_error_group_id;

    INSERT INTO observability.error_occurrences (
        error_group_id, occurred_at, severity, logger_name, component,
        exception_type, message, traceback, provider, media_kind, release,
        created_at
    )
    VALUES (
        v_error_group_id, p_occurred_at, p_severity, p_logger_name, p_component,
        p_exception_type, p_message, p_traceback, p_provider, p_media_kind,
        p_release, CURRENT_TIMESTAMP
    )
    RETURNING id INTO v_occurrence_id;

    INSERT INTO observability.error_request_links (
        error_occurrence_id, media_request_id
    )
    SELECT v_occurrence_id, request_id
    FROM unnest(COALESCE(p_media_request_ids, ARRAY[]::bigint[])) AS request_id
    ON CONFLICT DO NOTHING;

    RETURN v_occurrence_id;
END;
$$
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE VIEW observability.api_error_groups AS
SELECT
    id,
    fingerprint,
    display_name,
    event_code,
    exception_type,
    status,
    first_seen_at,
    last_seen_at,
    occurrence_count,
    first_release,
    last_release,
    linked_change,
    fixed_at,
    created_at,
    updated_at,
    fixed_at IS NOT NULL AND last_seen_at > fixed_at AS recurred_after_fix
FROM observability.error_groups
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE VIEW observability.api_error_occurrences AS
SELECT
    id,
    error_group_id,
    occurred_at,
    severity,
    logger_name,
    component,
    exception_type,
    message,
    traceback,
    provider,
    media_kind,
    release,
    created_at
FROM observability.error_occurrences
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE VIEW observability.api_reproduction_cases AS
SELECT
    links.error_occurrence_id AS occurrence_id,
    occurrences.error_group_id,
    groups.display_name,
    occurrences.occurred_at,
    requests.url,
    requests.normalized_url,
    occurrences.provider,
    occurrences.media_kind,
    occurrences.component,
    occurrences.release
FROM observability.error_request_links AS links
JOIN observability.error_occurrences AS occurrences
    ON occurrences.id = links.error_occurrence_id
JOIN observability.error_groups AS groups
    ON groups.id = occurrences.error_group_id
LEFT JOIN public.media_requests AS requests
    ON requests.id = links.media_request_id
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE VIEW observability.api_error_notes AS
SELECT id, error_group_id, note, actor, created_at
FROM observability.error_notes
            """
        )
    )
    apply_runtime_privileges(bind, *_runtime_roles())


def downgrade() -> None:
    bind = op.get_bind()
    _revoke_runtime_privileges(bind)
    op.execute(sa.text("DROP VIEW IF EXISTS observability.api_reproduction_cases"))
    op.execute(sa.text("DROP VIEW IF EXISTS observability.api_error_notes"))
    op.execute(sa.text("DROP VIEW IF EXISTS observability.api_error_occurrences"))
    op.execute(sa.text("DROP VIEW IF EXISTS observability.api_error_groups"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS observability.record_error"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_error_groups_updated_at ON observability.error_groups"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS observability.touch_error_group"))
    op.drop_index(
        "ix_error_notes_group_created_at", table_name="error_notes", schema=SCHEMA
    )
    op.drop_table("error_notes", schema=SCHEMA)
    op.drop_index(
        "ix_error_request_links_media_request_id",
        table_name="error_request_links",
        schema=SCHEMA,
    )
    op.drop_table("error_request_links", schema=SCHEMA)
    op.drop_index(
        "ix_error_occurrences_occurred_at",
        table_name="error_occurrences",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_error_occurrences_group_occurred_at",
        table_name="error_occurrences",
        schema=SCHEMA,
    )
    op.drop_table("error_occurrences", schema=SCHEMA)
    op.drop_table("error_groups", schema=SCHEMA)


def _runtime_roles() -> tuple[str, str]:
    app_user = os.getenv("DB_APP_USER")
    error_api_user = os.getenv("DB_ERROR_API_USER")
    if not app_user or not error_api_user:
        msg = "DB_APP_USER and DB_ERROR_API_USER are required for Error API migrations"
        raise RuntimeError(msg)
    validate_runtime_roles(app_user, error_api_user)
    return app_user, error_api_user


def _revoke_runtime_privileges(bind: sa.Connection) -> None:
    app_user, error_api_user = _runtime_roles()
    quote = bind.dialect.identifier_preparer.quote
    for user in (quote(app_user), quote(error_api_user)):
        bind.execute(
            sa.text(f"REVOKE ALL PRIVILEGES ON SCHEMA observability FROM {user}")
        )
