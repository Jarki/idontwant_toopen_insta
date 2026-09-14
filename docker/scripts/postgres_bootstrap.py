#!/usr/bin/env python3
"""PostgreSQL bootstrap — create roles, database, and grant privileges.

Idempotent. Fails closed on role/password drift. Never prints passwords.
Connects as the bootstrap (owner) user created by the postgres image,
then provisions migration and application roles and sets schema-level
privileges and default privileges.

Expected environment variables:
  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
  POSTGRES_USER, POSTGRES_PASSWORD          — bootstrap/owner credentials
  DB_MIGRATION_USER, DB_MIGRATION_PASSWORD   — migration role (DDL-capable, schema owner)
  DB_APP_USER, DB_APP_PASSWORD               — application (DML-only) role
  DB_ERROR_API_USER, DB_ERROR_API_PASSWORD   — Error API (curated access only) role
"""

import os
import sys
from typing import Any

MIGRATION_ONLY_TABLES = ("alembic_version", "reels")


def main() -> None:
    pg_host = os.environ.get("POSTGRES_HOST", "postgres")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db = os.environ.get("POSTGRES_DB", "")
    pg_user = os.environ.get("POSTGRES_USER", "")
    pg_password = os.environ.get("POSTGRES_PASSWORD", "")
    migration_user = os.environ.get("DB_MIGRATION_USER", "")
    migration_password = os.environ.get("DB_MIGRATION_PASSWORD", "")
    app_user = os.environ.get("DB_APP_USER", "")
    app_password = os.environ.get("DB_APP_PASSWORD", "")
    error_api_user = os.environ.get("DB_ERROR_API_USER", "")
    error_api_password = os.environ.get("DB_ERROR_API_PASSWORD", "")

    _require("POSTGRES_DB", pg_db)
    _require("POSTGRES_USER", pg_user)
    _require("POSTGRES_PASSWORD", pg_password)
    _require("DB_MIGRATION_USER", migration_user)
    _require("DB_MIGRATION_PASSWORD", migration_password)
    _require("DB_APP_USER", app_user)
    _require("DB_APP_PASSWORD", app_password)
    _require("DB_ERROR_API_USER", error_api_user)
    _require("DB_ERROR_API_PASSWORD", error_api_password)
    _require_distinct_roles(pg_user, migration_user, app_user, error_api_user)

    import psycopg

    # Phase 1 — connect to the maintenance database, create roles and database
    conn = psycopg.connect(
        host=pg_host,
        port=pg_port,
        dbname="postgres",
        user=pg_user,
        password=pg_password,
        autocommit=True,
    )
    try:
        cur = conn.cursor()

        # Verify we are connected as the bootstrap user
        cur.execute("SELECT current_user")
        actual = cur.fetchone()[0]
        if actual != pg_user:
            _die(f"Connected as {actual!r}, expected bootstrap user {pg_user!r}")

        _ensure_role(
            cur, migration_user, migration_password, host=pg_host, port=pg_port
        )
        _ensure_role(cur, app_user, app_password, host=pg_host, port=pg_port)
        _ensure_role(
            cur,
            error_api_user,
            error_api_password,
            host=pg_host,
            port=pg_port,
        )
        _ensure_database(cur, pg_db, migration_user)
    finally:
        conn.close()

    # Phase 2 — connect to the target database, set schema-level privileges
    conn = psycopg.connect(
        host=pg_host,
        port=pg_port,
        dbname=pg_db,
        user=pg_user,
        password=pg_password,
        autocommit=True,
    )
    try:
        cur = conn.cursor()

        # Public schema — restrict PUBLIC, grant only application roles that
        # need it. The Error API reaches application data solely through a
        # migration-owned observability view.
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cur.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {_q(migration_user)}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {_q(app_user)}")

        # Migration role — full DDL on existing objects
        cur.execute(
            f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
            f"TO {_q(migration_user)}"
        )
        cur.execute(
            f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
            f"TO {_q(migration_user)}"
        )

        # Application role — DML on existing runtime objects. Migration-only
        # tables are explicitly revoked below.
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f"TO {_q(app_user)}"
        )
        cur.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {_q(app_user)}")

        # Default privileges — objects created by migration role grant DML to app
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_q(migration_user)} "
            f"IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_q(app_user)}"
        )
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_q(migration_user)} "
            f"IN SCHEMA public "
            f"GRANT USAGE ON SEQUENCES TO {_q(app_user)}"
        )

        # Alembic metadata and legacy reels table are migration-only.
        for table in MIGRATION_ONLY_TABLES:
            if _relation_exists(cur, table):
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON TABLE {_q(table)} FROM {_q(app_user)}"
                )

        # Revoke CREATE on schema from app role (no DDL)
        cur.execute(f"REVOKE CREATE ON SCHEMA public FROM {_q(app_user)}")
        cur.execute(f"REVOKE ALL ON SCHEMA public FROM {_q(error_api_user)}")

        # Alembic requires the independent schema to exist before it can place
        # its version table there. Fail closed if an existing schema has an
        # unexpected owner.
        _ensure_schema(cur, "observability", migration_user)
        cur.execute("REVOKE ALL ON SCHEMA observability FROM PUBLIC")
        cur.execute(
            f"GRANT USAGE ON SCHEMA observability "
            f"TO {_q(app_user)}, {_q(error_api_user)}"
        )
        cur.execute(f"REVOKE CREATE ON SCHEMA observability FROM {_q(app_user)}")
        cur.execute(f"REVOKE CREATE ON SCHEMA observability FROM {_q(error_api_user)}")

        print("Privileges configured successfully")
    finally:
        conn.close()

    # Phase 3 — validate each role can connect and has expected capabilities
    _validate(
        pg_host, pg_port, pg_db, migration_user, migration_password, expect_ddl=True
    )
    _validate(pg_host, pg_port, pg_db, app_user, app_password, expect_ddl=False)
    _validate(
        pg_host,
        pg_port,
        pg_db,
        error_api_user,
        error_api_password,
        expect_ddl=False,
    )

    print("Bootstrap completed successfully")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _relation_exists(cur: Any, relation: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
    return cur.fetchone()[0] is not None


def _ensure_schema(cur: Any, schema: str, owner: str) -> None:
    cur.execute(
        "SELECT pg_catalog.pg_get_userbyid(nspowner) "
        "FROM pg_catalog.pg_namespace WHERE nspname = %s",
        (schema,),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(f"CREATE SCHEMA {_q(schema)} AUTHORIZATION {_q(owner)}")
        print(f"Created schema '{schema}' with owner '{owner}'")
        return
    if str(row[0]) != owner:
        _die(f"Schema '{schema}' exists with owner {str(row[0])!r}, expected {owner!r}")
    print(f"Schema '{schema}': exists, correct owner '{owner}'")


def _require(name: str, value: str) -> None:
    if not value:
        _die(f"Missing required environment variable: {name}")


def _require_distinct_roles(*roles: str) -> None:
    if len(roles) != len(set(roles)):
        _die(
            "PostgreSQL bootstrap, migration, application, and Error API roles must be distinct"
        )


def _die(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def _q(ident: str) -> str:
    """Quote a PostgreSQL identifier."""
    return f'"{ident.replace('"', '""')}"'


def _ensure_role(
    cur: Any,
    username: str,
    password: str,
    host: str,
    port: str,
) -> None:
    """Create a least-privilege login role or fail closed on role drift."""
    cur.execute(
        "SELECT r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
        "r.rolreplication, r.rolbypassrls, "
        "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m "
        "WHERE m.member = r.oid) "
        "FROM pg_catalog.pg_authid r WHERE r.rolname = %s",
        (username,),
    )
    attributes = cur.fetchone()
    exists = attributes is not None

    if not exists:
        from psycopg import sql

        cur.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(username),
                sql.Literal(password),
            ),
        )
        print(f"Created role '{username}'")
        return

    assert attributes is not None
    (
        can_login,
        is_superuser,
        can_create_db,
        can_create_role,
        can_replicate,
        can_bypass_rls,
        has_membership,
    ) = attributes
    if (
        not can_login
        or is_superuser
        or can_create_db
        or can_create_role
        or can_replicate
        or can_bypass_rls
        or has_membership
    ):
        _die(
            f"Role '{username}' has unexpected privileges; expected LOGIN, "
            "NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, "
            "NOBYPASSRLS, and no inherited role memberships"
        )

    # Role exists — validate password before continuing
    import psycopg

    try:
        test_conn = psycopg.connect(
            host=host,
            port=port,
            dbname="postgres",
            user=username,
            password=password,
            autocommit=True,
        )
        test_conn.close()
        print(f"Role '{username}': exists, password valid")
    except Exception:
        _die(
            f"Role '{username}' exists but the provided password does not match. "
            "Update the configured role password in .env or drop the role manually."
        )


def _ensure_database(cur: Any, dbname: str, owner: str) -> None:
    cur.execute(
        "SELECT pg_catalog.pg_get_userbyid(datdba) "
        "FROM pg_catalog.pg_database WHERE datname = %s",
        (dbname,),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(f"CREATE DATABASE {_q(dbname)} OWNER {_q(owner)}")
        print(f"Created database '{dbname}' with owner '{owner}'")
        return

    actual_owner = str(row[0])
    if actual_owner == owner:
        print(f"Database '{dbname}': exists, correct owner '{owner}'")
        return

    cur.execute("SELECT current_user")
    bootstrap_user = str(cur.fetchone()[0])
    if actual_owner != bootstrap_user:
        _die(
            f"Database '{dbname}' exists with owner "
            f"'{actual_owner}', expected bootstrap owner '{bootstrap_user}' "
            f"or migration owner '{owner}'"
        )
    cur.execute(f"ALTER DATABASE {_q(dbname)} OWNER TO {_q(owner)}")
    print(f"Transferred database '{dbname}' ownership to '{owner}'")


def _validate(
    host: str,
    port: str,
    dbname: str,
    user: str,
    password: str,
    expect_ddl: bool,
) -> None:
    """Verify the role can connect and (for migration role) execute DDL."""
    import psycopg

    connect_kwargs = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        "autocommit": True,
    }
    try:
        conn = psycopg.connect(**connect_kwargs)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
    except Exception as exc:
        _die(f"Role '{user}' connection check failed: {exc}")

    print(f"  '{user}': connection OK")

    if expect_ddl:
        try:
            conn = psycopg.connect(**connect_kwargs)
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS _pg_bootstrap_verify (id int)")
            cur.execute("DROP TABLE IF EXISTS _pg_bootstrap_verify")
            conn.close()
            print(f"  '{user}': DDL OK")
        except Exception as exc:
            _die(f"Migration role '{user}' expected DDL but failed: {exc}")
    else:
        try:
            conn = psycopg.connect(**connect_kwargs)
            cur = conn.cursor()
            cur.execute("CREATE TABLE _pg_bootstrap_verify (id int)")
            cur.execute("DROP TABLE IF EXISTS _pg_bootstrap_verify")
            conn.close()
            _die(f"Application role '{user}' unexpectedly has DDL privilege")
        except Exception:
            print(f"  '{user}': DDL correctly denied")


if __name__ == "__main__":
    main()
