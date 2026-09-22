#!/usr/bin/env python3
"""Provision the dashboard reader after migrations, separately from runtimes."""

import os
import sys

import psycopg
from postgres_bootstrap import (
    _ensure_role,
    _q,
    _remove_runtime_memberships,
    _require,
    _require_distinct_role_names,
)

GRANTS = {
    "public.media_requests": "created_at, telegram_user_id, telegram_chat_id, provider, delivered_at, failure_reason, media_item_id, completed_at",
    "observability.error_groups": "id, display_name, event_code, exception_type, status",
    "observability.error_occurrences": "error_group_id, occurred_at",
}


def main() -> None:
    names = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DB_MIGRATION_USER",
        "DB_APP_USER",
        "DB_ERROR_API_USER",
        "DB_DASHBOARD_USER",
        "DB_DASHBOARD_PASSWORD",
    )
    values = {name: os.environ.get(name, "") for name in names}
    for name, value in values.items():
        _require(name, value)
    _require_distinct_role_names(
        {name: value for name, value in values.items() if name.endswith("_USER")}
    )
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = values["DB_DASHBOARD_USER"]
    with (
        psycopg.connect(
            host=host,
            port=port,
            dbname=values["POSTGRES_DB"],
            user=values["POSTGRES_USER"],
            password=values["POSTGRES_PASSWORD"],
            autocommit=True,
        ) as connection,
        connection.cursor() as cursor,
    ):
        _ensure_role(
            cursor, user, values["DB_DASHBOARD_PASSWORD"], host, port, inherit=False
        )
        _remove_runtime_memberships(cursor, user, "dashboard")
        # Never repair ownership by granting even more privileges.
        cursor.execute(
            "SELECT 1 FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = %s) UNION ALL SELECT 1 FROM pg_namespace WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = %s) UNION ALL SELECT 1 FROM pg_database WHERE datdba = (SELECT oid FROM pg_roles WHERE rolname = %s)",
            (user, user, user),
        )
        if cursor.fetchone():
            raise ValueError("Dashboard reader must not own database objects")
        cursor.execute(f"ALTER ROLE {_q(user)} SET default_transaction_read_only = on")
        for schema in ("public", "observability"):
            cursor.execute(f"REVOKE ALL ON SCHEMA {schema} FROM {_q(user)}")
            for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
                cursor.execute(
                    f"REVOKE ALL ON ALL {kind} IN SCHEMA {schema} FROM {_q(user)}"
                )
            cursor.execute(f"GRANT USAGE ON SCHEMA {schema} TO {_q(user)}")
        for table, columns in GRANTS.items():
            cursor.execute(f"GRANT SELECT ({columns}) ON {table} TO {_q(user)}")
    # Authenticate and exercise exactly the curated columns before starting HTTP.
    with psycopg.connect(
        host=host,
        port=port,
        dbname=values["POSTGRES_DB"],
        user=user,
        password=values["DB_DASHBOARD_PASSWORD"],
        connect_timeout=5,
    ) as connection:
        for table, columns in GRANTS.items():
            connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    print("Dashboard reader ready")


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, ValueError):
        sys.exit(
            "Dashboard bootstrap failed; check database connectivity, role ownership and grants."
        )
