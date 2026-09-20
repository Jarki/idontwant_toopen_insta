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
  DB_APP_USER, DB_APP_PASSWORD               — bot application (DML-only) role
  DB_ERROR_API_USER, DB_ERROR_API_PASSWORD   — Error API (restricted) role
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
    _require_distinct_role_names(
        {
            "POSTGRES_USER": pg_user,
            "DB_MIGRATION_USER": migration_user,
            "DB_APP_USER": app_user,
            "DB_ERROR_API_USER": error_api_user,
        }
    )
    _require_distinct_passwords(
        {
            "POSTGRES_PASSWORD": pg_password,
            "DB_MIGRATION_PASSWORD": migration_password,
            "DB_APP_PASSWORD": app_password,
            "DB_ERROR_API_PASSWORD": error_api_password,
        }
    )
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
            inherit=False,
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

        # Public schema — restrict PUBLIC, grant to roles
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cur.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {_q(migration_user)}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {_q(app_user)}")

        _ensure_schema(cur, "observability", migration_user)
        _configure_runtime_isolation(cur, migration_user, app_user, error_api_user)
        _remove_runtime_memberships(cur, app_user, "bot")
        _remove_runtime_memberships(cur, error_api_user, "Error API")
        denied_relation = _representative_public_relation(cur)

        cur.execute("REVOKE ALL ON SCHEMA observability FROM PUBLIC")

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
        denied_relation=denied_relation,
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
        print(f"Created schema '{schema}' owned by '{owner}'")
        return

    actual_owner = str(row[0])
    if actual_owner != owner:
        _die(
            f"Schema '{schema}' exists with owner '{actual_owner}', "
            f"expected migration owner '{owner}'"
        )


def _require(name: str, value: str) -> None:
    if not value:
        _die(f"Missing required environment variable: {name}")


def _require_distinct_role_names(roles: dict[str, str]) -> None:
    names = list(roles.values())
    if len(set(names)) == len(names):
        return
    collisions = sorted(
        name for name in set(names) if sum(value == name for value in names) > 1
    )
    _die(
        "Security-boundary role names must be distinct; duplicated role name(s): "
        + ", ".join(repr(name) for name in collisions)
    )


def _require_distinct_passwords(passwords: dict[str, str]) -> None:
    names = list(passwords)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            if passwords[left_name] == passwords[right_name]:
                _die(
                    "Security-boundary database passwords must be distinct; "
                    f"{left_name} matches {right_name}"
                )


def _configure_runtime_isolation(
    cur: Any, migration_user: str, app_user: str, error_api_user: str
) -> None:
    migration = _q(migration_user)
    app = _q(app_user)
    error_api = _q(error_api_user)
    statements = (
        f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON SCHEMA observability FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA observability FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA observability FROM {error_api}",
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA observability FROM {error_api}",
    )
    for statement in statements:
        cur.execute(statement)

    for grantee in ("PUBLIC", app, error_api):
        for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration} "
                f"REVOKE ALL PRIVILEGES ON {object_type} FROM {grantee}"
            )
            for schema in ("public", "observability"):
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration} IN SCHEMA {schema} "
                    f"REVOKE ALL PRIVILEGES ON {object_type} FROM {grantee}"
                )


def _remove_runtime_memberships(cur: Any, user: str, role_label: str) -> None:
    membership_query = """
SELECT roles.rolname
FROM pg_catalog.pg_auth_members AS memberships
JOIN pg_catalog.pg_roles AS roles ON roles.oid = memberships.roleid
WHERE memberships.member = (
    SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s
)
ORDER BY roles.rolname
    """
    cur.execute(membership_query, (user,))
    memberships = [str(row[0]) for row in cur.fetchall()]
    for role in memberships:
        cur.execute(f"REVOKE {_q(role)} FROM {_q(user)}")

    cur.execute(membership_query, (user,))
    remaining_memberships = [str(row[0]) for row in cur.fetchall()]
    if remaining_memberships:
        _die(
            f"{role_label.capitalize()} role '{user}' retains role membership(s) after "
            f"repair: {', '.join(remaining_memberships)}"
        )


def _representative_public_relation(cur: Any) -> str | None:
    for relation in ("telegram_users", "media_requests", "media_items"):
        if _relation_exists(cur, relation):
            return relation
    return None


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
    inherit: bool = True,
) -> None:
    """Create a least-privilege login role or fail closed on role drift."""
    cur.execute(
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
        "rolreplication, rolbypassrl "
        "FROM pg_catalog.pg_authid WHERE rolname = %s",
        (username,),
    )
    attributes = cur.fetchone()
    exists = attributes is not None

    if not exists:
        from psycopg import sql

        role_options = (
            "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        if not inherit:
            role_options += " NOINHERIT"
        cur.execute(
            sql.SQL("CREATE ROLE {} WITH {} PASSWORD {}").format(
                sql.Identifier(username),
                sql.SQL(role_options),
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
        role_inherits,
        can_replicate,
        can_bypass_rls,
    ) = attributes
    if (
        not can_login
        or is_superuser
        or can_create_db
        or can_create_role
        or can_replicate
        or can_bypass_rls
    ):
        _die(
            f"Role '{username}' has unexpected privileges; expected LOGIN, "
            "NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, and NOBYPASSRLS"
        )
    if not inherit and role_inherits:
        cur.execute(f"ALTER ROLE {_q(username)} NOINHERIT")

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
    denied_relation: str | None = None,
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

    if denied_relation is not None:
        try:
            conn = psycopg.connect(**connect_kwargs)
            cur = conn.cursor()
            cur.execute(f"SELECT 1 FROM public.{_q(denied_relation)} LIMIT 1")
            conn.close()
            _die(f"Error API role '{user}' unexpectedly read public.{denied_relation}")
        except Exception:
            print(f"  '{user}': public application reads correctly denied")


if __name__ == "__main__":
    main()
