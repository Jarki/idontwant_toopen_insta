"""create independent observability error ledger

Revision ID: 20260908_0001
Revises: None
Create Date: 2026-09-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260908_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORD_ERROR_SIGNATURE = (
    "observability.record_error(varchar, varchar, varchar, varchar, "
    "timestamptz, varchar, varchar, varchar, varchar, varchar, varchar, "
    "varchar, varchar, bigint[])"
)


def _roles() -> tuple[str, str]:
    config = context.get_context().config
    if config is None:
        raise RuntimeError("Error API Alembic configuration is unavailable")
    app_user = config.attributes.get("db_app_user")
    api_user = config.attributes.get("db_error_api_user")
    if not isinstance(app_user, str) or not isinstance(api_user, str):
        raise RuntimeError("Error API migration role names were not configured")
    preparer = op.get_bind().dialect.identifier_preparer
    return preparer.quote(app_user), preparer.quote(api_user)


def upgrade() -> None:
    app_user, api_user = _roles()

    op.create_table(
        "error_groups",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("event_code", sa.String(128), nullable=False),
        sa.Column("exception_type", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), server_default="new", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "occurrence_count", sa.BigInteger(), server_default="1", nullable=False
        ),
        sa.Column("first_release", sa.String(128), nullable=True),
        sa.Column("last_release", sa.String(128), nullable=True),
        sa.Column("linked_change", sa.String(500), nullable=True),
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "occurrence_count >= 1", name="ck_error_groups_count_positive"
        ),
        sa.CheckConstraint(
            "status IN ('new', 'investigating', 'fixing', 'monitoring', 'resolved', 'ignored')",
            name="ck_error_groups_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
        schema="observability",
    )
    op.create_table(
        "error_occurrences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("error_group_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("logger_name", sa.String(128), nullable=False),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("exception_type", sa.String(256), nullable=True),
        sa.Column("message", sa.String(4000), nullable=False),
        sa.Column("traceback", sa.String(32000), nullable=True),
        sa.Column("provider", sa.String(128), nullable=True),
        sa.Column("media_kind", sa.String(128), nullable=True),
        sa.Column("release", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('ERROR', 'CRITICAL')", name="ck_error_occurrences_severity"
        ),
        sa.ForeignKeyConstraint(["error_group_id"], ["observability.error_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="observability",
    )
    op.create_index(
        "ix_error_occurrences_occurred_at",
        "error_occurrences",
        [sa.literal_column("occurred_at DESC")],
        schema="observability",
    )
    op.create_index(
        "ix_error_occurrences_group_occurred",
        "error_occurrences",
        ["error_group_id", sa.literal_column("occurred_at DESC")],
        schema="observability",
    )
    op.create_table(
        "error_request_links",
        sa.Column("error_occurrence_id", sa.BigInteger(), nullable=False),
        sa.Column("media_request_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["error_occurrence_id"], ["observability.error_occurrences.id"]
        ),
        sa.PrimaryKeyConstraint("error_occurrence_id", "media_request_id"),
        schema="observability",
    )
    op.create_table(
        "error_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("error_group_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(note) BETWEEN 1 AND 4000", name="ck_error_notes_note_length"
        ),
        sa.ForeignKeyConstraint(["error_group_id"], ["observability.error_groups.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="observability",
    )
    op.create_index(
        "ix_error_notes_group_created",
        "error_notes",
        ["error_group_id", sa.literal_column("created_at DESC")],
        schema="observability",
    )

    op.execute("""
        CREATE VIEW observability.api_error_groups AS
        SELECT id, fingerprint, display_name, event_code, exception_type, status,
               first_seen_at, last_seen_at, occurrence_count, first_release,
               last_release, linked_change, fixed_at, created_at, updated_at
          FROM observability.error_groups
    """)
    op.execute("""
        CREATE VIEW observability.api_error_occurrences AS
        SELECT id, error_group_id, occurred_at, severity, logger_name, component,
               exception_type, message, traceback, provider, media_kind, release,
               created_at
          FROM observability.error_occurrences
    """)
    op.execute("""
        CREATE VIEW observability.api_error_notes AS
        SELECT id, error_group_id, note, actor, created_at
          FROM observability.error_notes
    """)
    op.execute("""
        CREATE VIEW observability.api_reproduction_cases AS
        SELECT g.id AS error_group_id,
               o.id AS error_occurrence_id,
               g.display_name,
               o.occurred_at,
               r.url AS submitted_url,
               r.normalized_url,
               o.provider,
               o.media_kind,
               o.component AS failure_stage,
               o.release
          FROM observability.error_request_links l
          JOIN observability.error_occurrences o ON o.id = l.error_occurrence_id
          JOIN observability.error_groups g ON g.id = o.error_group_id
          LEFT JOIN public.media_requests r ON r.id = l.media_request_id
    """)

    op.execute("""
        CREATE FUNCTION observability.record_error(
            p_fingerprint varchar,
            p_display_name varchar,
            p_event_code varchar,
            p_exception_type varchar,
            p_occurred_at timestamptz,
            p_severity varchar,
            p_logger_name varchar,
            p_component varchar,
            p_message varchar,
            p_traceback varchar,
            p_provider varchar,
            p_media_kind varchar,
            p_release varchar,
            p_media_request_ids bigint[]
        ) RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, observability
        AS $function$
        DECLARE
            v_group_id bigint;
            v_occurrence_id bigint;
            v_request_id bigint;
        BEGIN
            IF p_fingerprint IS NULL OR length(p_fingerprint) NOT BETWEEN 1 AND 64
               OR p_display_name IS NULL OR length(p_display_name) NOT BETWEEN 1 AND 200
               OR p_event_code IS NULL OR length(p_event_code) NOT BETWEEN 1 AND 128
               OR p_occurred_at IS NULL
               OR p_severity IS NULL OR p_severity NOT IN ('ERROR', 'CRITICAL')
               OR p_logger_name IS NULL OR length(p_logger_name) NOT BETWEEN 1 AND 128
               OR p_component IS NULL OR length(p_component) NOT BETWEEN 1 AND 128
               OR p_message IS NULL OR length(p_message) > 4000
               OR length(coalesce(p_exception_type, '')) > 256
               OR length(coalesce(p_traceback, '')) > 32000
               OR length(coalesce(p_provider, '')) > 128
               OR length(coalesce(p_media_kind, '')) > 128
               OR length(coalesce(p_release, '')) > 128
               OR cardinality(coalesce(p_media_request_ids, ARRAY[]::bigint[])) > 20
            THEN
                RAISE EXCEPTION 'invalid bounded error event input' USING ERRCODE = '22023';
            END IF;

            INSERT INTO observability.error_groups (
                fingerprint, display_name, event_code, exception_type,
                first_seen_at, last_seen_at, occurrence_count,
                first_release, last_release
            ) VALUES (
                p_fingerprint, p_display_name, p_event_code, p_exception_type,
                p_occurred_at, p_occurred_at, 1, p_release, p_release
            )
            ON CONFLICT (fingerprint) DO UPDATE SET
                first_seen_at = least(
                    observability.error_groups.first_seen_at,
                    EXCLUDED.first_seen_at
                ),
                last_seen_at = greatest(
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
                    THEN EXCLUDED.last_release
                    ELSE observability.error_groups.last_release
                END,
                updated_at = now()
            RETURNING id INTO v_group_id;

            INSERT INTO observability.error_occurrences (
                error_group_id, occurred_at, severity, logger_name, component,
                exception_type, message, traceback, provider, media_kind, release
            ) VALUES (
                v_group_id, p_occurred_at, p_severity, p_logger_name, p_component,
                p_exception_type, p_message, p_traceback, p_provider, p_media_kind,
                p_release
            ) RETURNING id INTO v_occurrence_id;

            FOREACH v_request_id IN ARRAY coalesce(p_media_request_ids, ARRAY[]::bigint[])
            LOOP
                IF v_request_id IS NULL OR v_request_id <= 0 THEN
                    RAISE EXCEPTION 'invalid media request id' USING ERRCODE = '22023';
                END IF;
                INSERT INTO observability.error_request_links (
                    error_occurrence_id, media_request_id
                ) VALUES (v_occurrence_id, v_request_id)
                ON CONFLICT DO NOTHING;
            END LOOP;

            RETURN v_occurrence_id;
        END;
        $function$
    """)

    # Start from no runtime access, then grant the exact contract.
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA observability FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA observability FROM PUBLIC")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_RECORD_ERROR_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON ALL TABLES IN SCHEMA observability FROM {app_user}, {api_user}"
    )
    op.execute(
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA observability FROM {app_user}, {api_user}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA observability TO {app_user}, {api_user}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_RECORD_ERROR_SIGNATURE} TO {app_user}")
    op.execute(
        f"GRANT SELECT ON observability.api_error_groups, observability.api_error_occurrences, observability.api_reproduction_cases, observability.api_error_notes TO {api_user}"
    )
    op.execute(
        f"GRANT UPDATE (display_name, status, linked_change, fixed_at) "
        f"ON observability.api_error_groups TO {api_user}"
    )
    op.execute(
        f"GRANT INSERT (error_group_id, note, actor) ON observability.error_notes TO {api_user}"
    )
    op.execute(
        f"GRANT USAGE ON SEQUENCE observability.error_notes_id_seq TO {api_user}"
    )
    op.execute(
        f"REVOKE ALL ON TABLE observability.alembic_version_error_api FROM {app_user}, {api_user}"
    )


def downgrade() -> None:
    app_user, api_user = _roles()
    op.execute(
        f"REVOKE ALL ON ALL TABLES IN SCHEMA observability FROM {app_user}, {api_user}"
    )
    op.execute(
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA observability FROM {app_user}, {api_user}"
    )
    op.execute(f"DROP FUNCTION {_RECORD_ERROR_SIGNATURE}")
    op.execute("DROP VIEW observability.api_reproduction_cases")
    op.execute("DROP VIEW observability.api_error_notes")
    op.execute("DROP VIEW observability.api_error_occurrences")
    op.execute("DROP VIEW observability.api_error_groups")
    op.drop_index(
        "ix_error_notes_group_created", table_name="error_notes", schema="observability"
    )
    op.drop_table("error_notes", schema="observability")
    op.drop_table("error_request_links", schema="observability")
    op.drop_index(
        "ix_error_occurrences_group_occurred",
        table_name="error_occurrences",
        schema="observability",
    )
    op.drop_index(
        "ix_error_occurrences_occurred_at",
        table_name="error_occurrences",
        schema="observability",
    )
    op.drop_table("error_occurrences", schema="observability")
    op.drop_table("error_groups", schema="observability")
