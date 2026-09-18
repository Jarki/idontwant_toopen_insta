"""Reapply the observability runtime privilege boundary."""

from __future__ import annotations

import sqlalchemy as sa


def validate_runtime_roles(app_user: str, error_api_user: str) -> None:
    """Reject a configuration that would merge the isolated runtime capabilities."""
    if app_user == error_api_user:
        msg = "DB_APP_USER and DB_ERROR_API_USER must name distinct roles"
        raise RuntimeError(msg)


def apply_runtime_privileges(
    bind: sa.Connection, app_user: str, error_api_user: str
) -> None:
    """Remove stale grants and restore only the approved runtime capabilities."""
    validate_runtime_roles(app_user, error_api_user)
    quote = bind.dialect.identifier_preparer.quote
    app = quote(app_user)
    error_api = quote(error_api_user)
    statements = (
        "REVOKE ALL PRIVILEGES ON SCHEMA observability FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA observability FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA observability FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA observability FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA observability FROM {app}",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA observability FROM {app}",
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA observability FROM {app}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON TABLES FROM {app}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON SEQUENCES FROM {app}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON FUNCTIONS FROM {app}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON TABLES FROM {app}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {app}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON FUNCTIONS FROM {app}",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA observability FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA observability FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA observability FROM {error_api}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON TABLES FROM {error_api}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON SEQUENCES FROM {error_api}",
        f"ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON FUNCTIONS FROM {error_api}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON TABLES FROM {error_api}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {error_api}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA observability "
        f"REVOKE ALL PRIVILEGES ON FUNCTIONS FROM {error_api}",
        f"GRANT USAGE ON SCHEMA observability TO {app}",
        "GRANT EXECUTE ON FUNCTION observability.record_error("
        "text, text, text, timestamptz, text, text, text, text, text, text, "
        f"text, text, text, bigint[]) TO {app}",
        f"GRANT USAGE ON SCHEMA observability TO {error_api}",
        f"GRANT SELECT ON observability.api_error_groups TO {error_api}",
        f"GRANT SELECT ON observability.api_error_occurrences TO {error_api}",
        f"GRANT SELECT ON observability.api_reproduction_cases TO {error_api}",
        f"GRANT SELECT ON observability.api_error_notes TO {error_api}",
        f"GRANT SELECT (id) ON observability.error_groups TO {error_api}",
        f"GRANT UPDATE (display_name, status, linked_change, fixed_at) "
        f"ON observability.error_groups TO {error_api}",
        f"GRANT INSERT ON observability.error_notes TO {error_api}",
        f"GRANT USAGE ON SEQUENCE observability.error_notes_id_seq TO {error_api}",
    )
    for statement in statements:
        bind.execute(sa.text(statement))
