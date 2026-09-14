from __future__ import annotations

from alembic import command
from sqlalchemy import Engine, inspect, text

from .conftest import ObservabilityDatabase


def test_independent_versions_and_schema_owner(
    admin_engine: Engine,
    observability_database: ObservabilityDatabase,
) -> None:
    with admin_engine.connect() as connection:
        main_revision = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        error_revision = connection.scalar(
            text("SELECT version_num FROM observability.alembic_version_error_api")
        )
        schema_owner = connection.scalar(
            text(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
                "WHERE nspname = 'observability'"
            )
        )

    assert main_revision == "20260718_0007"
    assert error_revision == "20260908_0001"
    assert schema_owner == observability_database.migration_user


def test_error_api_downgrade_preserves_public_data(
    admin_engine: Engine,
    observability_database: ObservabilityDatabase,
) -> None:
    error_config = observability_database.error_config
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO telegram_users "
                "(id, first_name, is_bot, created_at, updated_at) "
                "VALUES (987654321, 'preserved', false, now(), now())"
            )
        )

    command.downgrade(error_config, "base")
    try:
        with admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT first_name FROM telegram_users WHERE id = 987654321")
                )
                == "preserved"
            )
            assert not inspect(connection).has_table(
                "error_groups", schema="observability"
            )
            assert inspect(connection).has_table("media_requests", schema="public")
    finally:
        command.upgrade(error_config, "head")


def test_main_metadata_does_not_include_observability(admin_engine: Engine) -> None:
    with admin_engine.connect() as connection:
        public_tables = set(inspect(connection).get_table_names(schema="public"))
        observability_tables = set(
            inspect(connection).get_table_names(schema="observability")
        )

    assert "error_groups" not in public_tables
    assert {
        "error_groups",
        "error_occurrences",
        "error_request_links",
        "error_notes",
    }.issubset(observability_tables)
