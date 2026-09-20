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


def test_bootstrap_rejects_colliding_security_boundary_passwords(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "shared-secret-must-not-leak"
    with pytest.raises(SystemExit):
        postgres_bootstrap._require_distinct_passwords(
            {
                "POSTGRES_PASSWORD": "owner-secret",
                "DB_MIGRATION_PASSWORD": "migration-secret",
                "DB_APP_PASSWORD": secret,
                "DB_ERROR_API_PASSWORD": secret,
            }
        )

    assert secret not in capsys.readouterr().err


@pytest.mark.parametrize(
    "attributes",
    [
        (True, False, False, False, True, True, False),
        (True, False, False, False, True, False, True),
    ],
)
def test_bootstrap_rejects_replication_and_bypassrls_roles_without_secrets(
    attributes: tuple[bool, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "role-password-must-not-leak"

    class RoleCursor:
        def execute(self, _statement: object, *_args: object) -> None:
            pass

        def fetchone(self) -> tuple[bool, ...]:
            return attributes

    with pytest.raises(SystemExit):
        postgres_bootstrap._ensure_role(
            RoleCursor(),
            "db_app",
            secret,
            host="postgres",
            port="5432",
        )

    assert secret not in capsys.readouterr().err


def test_bootstrap_removes_unsafe_runtime_default_privileges() -> None:
    cursor = _RecordingCursor()

    postgres_bootstrap._configure_runtime_isolation(
        cursor, "db_migration", "db_app", "db_error_api"
    )

    assert (
        'REVOKE ALL PRIVILEGES ON SCHEMA public FROM "db_error_api"'
        in cursor.statements
    )
    for grantee in ("PUBLIC", '"db_app"', '"db_error_api"'):
        for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            assert (
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" '
                f"REVOKE ALL PRIVILEGES ON {object_type} FROM {grantee}"
            ) in cursor.statements
            for schema in ("public", "observability"):
                assert (
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE "db_migration" '
                    f"IN SCHEMA {schema} REVOKE ALL PRIVILEGES ON {object_type} "
                    f"FROM {grantee}"
                ) in cursor.statements


def test_bootstrap_removes_every_runtime_role_membership() -> None:
    cursor = _RecordingCursor([[("pg_read_all_data",), ("runtime_group",)], []])

    postgres_bootstrap._remove_runtime_memberships(cursor, "db_app", "bot")

    assert cursor.statements[1:3] == [
        'REVOKE "pg_read_all_data" FROM "db_app"',
        'REVOKE "runtime_group" FROM "db_app"',
    ]


def test_bootstrap_fails_closed_when_membership_repair_does_not_converge() -> None:
    cursor = _RecordingCursor([[("pg_read_all_data",)], [("pg_read_all_data",)]])

    with pytest.raises(SystemExit):
        postgres_bootstrap._remove_runtime_memberships(
            cursor, "db_error_api", "Error API"
        )
