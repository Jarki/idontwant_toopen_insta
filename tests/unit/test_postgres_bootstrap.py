from __future__ import annotations

import pytest

from docker.scripts import postgres_bootstrap


class _RecordingCursor:
    def __init__(
        self, membership_results: list[list[tuple[str]]] | None = None
    ) -> None:
        self.statements: list[str] = []
        self.membership_results = membership_results or []

    def execute(self, statement: str, *args: object) -> None:
        del args
        self.statements.append(statement)

    def fetchall(self) -> list[tuple[str]]:
        return self.membership_results.pop(0)


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
        'REVOKE ALL PRIVILEGES ON SCHEMA observability FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA observability FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA observability FROM "db_error_api"',
        'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA observability FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA observability '
        'REVOKE ALL PRIVILEGES ON TABLES FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA observability '
        'REVOKE ALL PRIVILEGES ON SEQUENCES FROM "db_error_api"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" IN SCHEMA observability '
        'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM "db_error_api"',
    ]


def test_bootstrap_removes_every_error_api_role_membership() -> None:
    cursor = _RecordingCursor([[("pg_read_all_data",), ("runtime_group",)], []])

    postgres_bootstrap._remove_error_api_memberships(cursor, "db_error_api")

    assert cursor.statements[1:3] == [
        'REVOKE "pg_read_all_data" FROM "db_error_api"',
        'REVOKE "runtime_group" FROM "db_error_api"',
    ]


def test_bootstrap_fails_closed_when_membership_repair_does_not_converge() -> None:
    cursor = _RecordingCursor([[("pg_read_all_data",)], [("pg_read_all_data",)]])

    with pytest.raises(SystemExit):
        postgres_bootstrap._remove_error_api_memberships(cursor, "db_error_api")
