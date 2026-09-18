from __future__ import annotations

import pytest

from docker.scripts import postgres_bootstrap


class _RecordingCursor:
    def __init__(self, membership_roles: list[tuple[str]] | None = None) -> None:
        self.statements: list[str] = []
        self.membership_roles = membership_roles or []

    def execute(self, statement: str, *args: object) -> None:
        del args
        self.statements.append(statement)

    def fetchall(self) -> list[tuple[str]]:
        return self.membership_roles


def test_bootstrap_rejects_colliding_security_boundary_roles() -> None:
    with pytest.raises(SystemExit):
        postgres_bootstrap._require_distinct_role_names(
            {
                "POSTGRES_USER": "db_owner",
                "DB_MIGRATION_USER": "db_migration",
                "DB_APP_USER": "db_error_api",
                "DB_ERROR_API_USER": "db_error_api",
            }
        )


def test_bootstrap_removes_direct_error_api_public_privileges() -> None:
    cursor = _RecordingCursor()

    postgres_bootstrap._configure_error_api_isolation(
        cursor, "db_migration", "db_error_api"
    )

    assert cursor.statements == [
        'REVOKE ALL PRIVILEGES ON SCHEMA public FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA public '
        'REVOKE ALL PRIVILEGES ON TABLES FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA public '
        'REVOKE ALL PRIVILEGES ON SEQUENCES FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA public '
        'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM "db_error_api"',
    ]


def test_bootstrap_rejects_unsafe_error_api_membership() -> None:
    cursor = _RecordingCursor([("db_app",)])

    with pytest.raises(SystemExit):
        postgres_bootstrap._reject_unsafe_error_api_memberships(cursor, "db_error_api")
