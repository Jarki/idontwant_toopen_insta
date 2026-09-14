from __future__ import annotations

from typing import Any

import pytest

from docker.scripts import postgres_bootstrap


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...] | None]) -> None:
        self.rows = iter(rows)
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, statement: str, parameters: object | None = None) -> None:
        self.executed.append((statement, parameters))

    def fetchone(self) -> tuple[Any, ...] | None:
        return next(self.rows)


def test_ensure_schema_creates_missing_schema_for_migration_owner() -> None:
    cursor = FakeCursor([None])

    postgres_bootstrap._ensure_schema(cursor, "observability", "db_migration")

    assert cursor.executed[-1][0] == (
        'CREATE SCHEMA "observability" AUTHORIZATION "db_migration"'
    )


def test_ensure_schema_fails_closed_on_owner_drift() -> None:
    cursor = FakeCursor([("unexpected_owner",)])

    with pytest.raises(SystemExit):
        postgres_bootstrap._ensure_schema(cursor, "observability", "db_migration")


def test_role_names_must_be_distinct() -> None:
    with pytest.raises(SystemExit):
        postgres_bootstrap._require_distinct_roles(
            "db_owner",
            "db_migration",
            "db_app",
            "db_app",
        )


def test_existing_role_with_elevated_capability_is_rejected() -> None:
    # login, superuser, createdb, createrole, replication, bypassrls, membership
    cursor = FakeCursor([(True, False, False, False, True, False, False)])

    with pytest.raises(SystemExit):
        postgres_bootstrap._ensure_role(
            cursor,
            "db_error_api",
            "test-password",
            host="localhost",
            port="5432",
        )


def test_existing_role_with_inherited_membership_is_rejected() -> None:
    cursor = FakeCursor([(True, False, False, False, False, False, True)])

    with pytest.raises(SystemExit):
        postgres_bootstrap._ensure_role(
            cursor,
            "db_error_api",
            "test-password",
            host="localhost",
            port="5432",
        )
