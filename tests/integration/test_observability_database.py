"""PostgreSQL contracts for the isolated observability ledger.

Set all OBSERVABILITY_TEST_* URLs to a disposable database provisioned by the
bootstrap service. The test migrates the public schema first and owns the
observability migration lifecycle.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def database_urls() -> dict[str, str]:
    names = {
        "migration": "OBSERVABILITY_TEST_MIGRATION_URL",
        "bot": "OBSERVABILITY_TEST_BOT_URL",
        "error_api": "OBSERVABILITY_TEST_ERROR_API_URL",
    }
    values = {key: os.getenv(name, "") for key, name in names.items()}
    if not all(values.values()):
        pytest.skip("observability PostgreSQL test URLs are not configured")
    return values


@pytest.fixture(scope="module")
def migration_roles() -> None:
    if not os.getenv("DB_APP_USER") or not os.getenv("DB_ERROR_API_USER"):
        pytest.skip("DB_APP_USER and DB_ERROR_API_USER are required")


@pytest.fixture(scope="module")
def bootstrap_environment() -> dict[str, str]:
    names = (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DB_MIGRATION_USER",
        "DB_MIGRATION_PASSWORD",
        "DB_APP_USER",
        "DB_APP_PASSWORD",
        "DB_ERROR_API_USER",
        "DB_ERROR_API_PASSWORD",
    )
    values = {name: os.getenv(name, "") for name in names}
    if not all(values.values()):
        pytest.skip("bootstrap PostgreSQL environment is not configured")
    return values


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    original = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _upgrade(config_name: str, url: str) -> None:
    config = Config(str(PROJECT_ROOT / config_name))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


def _run_error_api_upgrade(url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "error_api_alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DB_MIGRATION_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


def _downgrade_error_api(url: str) -> None:
    config = Config(str(PROJECT_ROOT / "error_api_alembic.ini"))
    config.attributes["database_url"] = url
    command.downgrade(config, "base")


def _rerun_bootstrap(environment: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "docker/scripts/postgres_bootstrap.py"],
        cwd=PROJECT_ROOT,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def engines(
    database_urls: dict[str, str], migration_roles: None
) -> Iterator[dict[str, Engine]]:
    with _environment(
        DB_APP_USER=os.environ["DB_APP_USER"],
        DB_ERROR_API_USER=os.environ["DB_ERROR_API_USER"],
    ):
        _upgrade("alembic.ini", database_urls["migration"])
        _upgrade("error_api_alembic.ini", database_urls["migration"])

    result = {name: create_engine(url) for name, url in database_urls.items()}
    try:
        yield result
    finally:
        for engine in result.values():
            engine.dispose()


@pytest.fixture(autouse=True)
def clean_ledger(engines: dict[str, Engine]) -> None:
    with engines["migration"].begin() as connection:
        connection.execute(
            text(
                "TRUNCATE observability.error_request_links, "
                "observability.error_notes, observability.error_occurrences, "
                "observability.error_groups RESTART IDENTITY"
            )
        )


def _event(
    fingerprint: str,
    *,
    occurred_at: dt.datetime | None = None,
    request_ids: list[int] | None = None,
) -> dict[str, object]:
    return {
        "fingerprint": fingerprint,
        "display_name": "Downloader failed",
        "event_code": "downloader.failed",
        "occurred_at": occurred_at or dt.datetime.now(dt.UTC),
        "severity": "ERROR",
        "logger_name": "ig_reel_downloader.downloaders",
        "component": "download",
        "exception_type": "RuntimeError",
        "message": "sanitized failure",
        "traceback": "Traceback (most recent call last): sanitized",
        "provider": "instagram",
        "media_kind": "reel",
        "release": "test-release",
        "request_ids": request_ids or [],
    }


def _record(engine: Engine, event: dict[str, object]) -> int:
    statement = text(
        """
SELECT observability.record_error(
    :fingerprint, :display_name, :event_code, :occurred_at, :severity,
    :logger_name, :component, :exception_type, :message, :traceback,
    :provider, :media_kind, :release, CAST(:request_ids AS bigint[])
)
        """
    )
    with engine.begin() as connection:
        return int(connection.execute(statement, event).scalar_one())


def _denied(engine: Engine, statement: str) -> None:
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(text(statement))


def test_streams_apply_independently_and_report_current(
    engines: dict[str, Engine],
) -> None:
    with engines["migration"].connect() as connection:
        main_revision = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one()
        error_revision = connection.execute(
            text("SELECT version_num FROM observability.alembic_version_error_api")
        ).scalar_one()
        assert (
            connection.execute(
                text("SELECT to_regclass('public.media_requests')")
            ).scalar_one()
            == "media_requests"
        )

    assert main_revision == "20260718_0007"
    assert error_revision == "20260918_0001"


def test_error_api_migrations_use_the_migration_url(
    database_urls: dict[str, str],
) -> None:
    output = StringIO()
    config = Config(str(PROJECT_ROOT / "error_api_alembic.ini"), stdout=output)
    with _environment(
        DB_MIGRATION_URL=database_urls["migration"],
        ERROR_API_DATABASE_URL=database_urls["error_api"],
    ):
        command.current(config)

    assert "20260918_0001" in output.getvalue()


def test_recording_reuses_group_and_keeps_occurrences_immutable(
    engines: dict[str, Engine],
) -> None:
    first = _record(engines["bot"], _event("same-fingerprint", request_ids=[999, 999]))
    second = _record(engines["bot"], _event("same-fingerprint", request_ids=[999]))

    assert first != second
    with engines["migration"].connect() as connection:
        group = connection.execute(
            text(
                "SELECT occurrence_count FROM observability.error_groups "
                "WHERE fingerprint = 'same-fingerprint'"
            )
        ).scalar_one()
        occurrences = connection.execute(
            text("SELECT count(*) FROM observability.error_occurrences")
        ).scalar_one()
        links = connection.execute(
            text("SELECT count(*) FROM observability.error_request_links")
        ).scalar_one()
        reproduction = connection.execute(
            text(
                "SELECT url, normalized_url, provider, media_kind "
                "FROM observability.api_reproduction_cases WHERE occurrence_id = :id"
            ),
            {"id": first},
        ).one()

    assert group == 2
    assert occurrences == 2
    assert links == 2
    assert reproduction == (None, None, "instagram", "reel")


def test_recording_rolls_back_group_when_occurrence_insert_fails(
    engines: dict[str, Engine],
) -> None:
    with engines["migration"].begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE observability.error_occurrences "
                "ADD CONSTRAINT ck_test_occurrence_failure CHECK (false)"
            )
        )
    try:
        with pytest.raises(DBAPIError):
            _record(engines["bot"], _event("rolled-back-fingerprint"))
    finally:
        with engines["migration"].begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE observability.error_occurrences "
                    "DROP CONSTRAINT ck_test_occurrence_failure"
                )
            )

    with engines["migration"].connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observability.error_groups "
                    "WHERE fingerprint = 'rolled-back-fingerprint'"
                )
            ).scalar_one()
            == 0
        )


def test_concurrent_recording_preserves_group_count(
    engines: dict[str, Engine],
) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        occurrence_ids = list(
            executor.map(
                lambda _: _record(engines["bot"], _event("concurrent-fingerprint")),
                range(16),
            )
        )

    assert len(set(occurrence_ids)) == 16
    with engines["migration"].connect() as connection:
        count = connection.execute(
            text(
                "SELECT occurrence_count FROM observability.error_groups "
                "WHERE fingerprint = 'concurrent-fingerprint'"
            )
        ).scalar_one()
        occurrences = connection.execute(
            text(
                "SELECT count(*) FROM observability.error_occurrences "
                "WHERE error_group_id = (SELECT id FROM observability.error_groups "
                "WHERE fingerprint = 'concurrent-fingerprint')"
            )
        ).scalar_one()

    assert count == 16
    assert occurrences == 16


def test_recurrence_preserves_triage_fields(engines: dict[str, Engine]) -> None:
    _record(engines["bot"], _event("triaged-fingerprint"))
    fixed_at = dt.datetime.now(dt.UTC)
    with engines["error_api"].begin() as connection:
        connection.execute(
            text(
                "UPDATE observability.error_groups SET display_name = :name, "
                "status = 'resolved', linked_change = 'abc123', fixed_at = :fixed_at "
                "WHERE id = (SELECT id FROM observability.api_error_groups "
                "WHERE fingerprint = 'triaged-fingerprint')"
            ),
            {"name": "Edited title", "fixed_at": fixed_at},
        )
    _record(
        engines["bot"],
        _event("triaged-fingerprint", occurred_at=fixed_at + dt.timedelta(seconds=1)),
    )

    with engines["migration"].connect() as connection:
        group = connection.execute(
            text(
                "SELECT display_name, status, linked_change, fixed_at, occurrence_count "
                "FROM observability.error_groups WHERE fingerprint = 'triaged-fingerprint'"
            )
        ).one()
        regressed = connection.execute(
            text(
                "SELECT recurred_after_fix FROM observability.api_error_groups "
                "WHERE fingerprint = 'triaged-fingerprint'"
            )
        ).scalar_one()

    assert group[0:3] == ("Edited title", "resolved", "abc123")
    assert group[3] == fixed_at
    assert group[4] == 2
    assert regressed is True


def test_runtime_roles_are_restricted_to_intended_capabilities(
    engines: dict[str, Engine],
) -> None:
    occurrence_id = _record(engines["bot"], _event("privilege-fingerprint"))

    with engines["error_api"].connect() as connection:
        assert (
            connection.execute(
                text("SELECT fingerprint FROM observability.api_error_groups")
            ).scalar_one()
            == "privilege-fingerprint"
        )
    with engines["error_api"].begin() as connection:
        connection.execute(
            text(
                "INSERT INTO observability.error_notes "
                "(error_group_id, note, actor, created_at) "
                "VALUES (1, 'triage note', 'test', CURRENT_TIMESTAMP)"
            )
        )

    _denied(engines["bot"], "SELECT * FROM observability.error_groups")
    _denied(engines["error_api"], "SELECT display_name FROM observability.error_groups")
    _denied(
        engines["error_api"],
        "SELECT observability.record_error("
        "'x', 'x', 'x', CURRENT_TIMESTAMP, 'ERROR', 'x', NULL, NULL, "
        "'x', NULL, NULL, NULL, NULL, ARRAY[]::bigint[])",
    )
    _denied(engines["bot"], "SELECT * FROM public.alembic_version")
    _denied(engines["bot"], "SELECT * FROM observability.alembic_version_error_api")
    _denied(
        engines["bot"],
        "UPDATE observability.error_occurrences SET severity = 'CRITICAL'",
    )
    _denied(engines["bot"], "DELETE FROM observability.error_occurrences")
    _denied(engines["error_api"], "SELECT * FROM public.telegram_users")
    _denied(engines["error_api"], "SELECT * FROM public.media_requests")
    _denied(engines["error_api"], "SELECT * FROM public.media_items")
    _denied(engines["error_api"], "SELECT * FROM public.media_assets")
    _denied(engines["error_api"], "SELECT * FROM public.judgmental_animations")
    _denied(engines["error_api"], "SELECT * FROM public.alembic_version")
    _denied(
        engines["error_api"], "SELECT * FROM observability.alembic_version_error_api"
    )
    _denied(
        engines["error_api"],
        "UPDATE observability.error_occurrences SET severity = 'CRITICAL'",
    )
    _denied(engines["error_api"], "DELETE FROM observability.error_occurrences")
    _denied(engines["error_api"], "CREATE TABLE observability.denied_ddl (id int)")

    with engines["migration"].connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM observability.error_occurrences WHERE id = :id"
                ),
                {"id": occurrence_id},
            ).scalar_one()
            == 1
        )


def test_error_api_migration_rerun_repairs_stale_direct_grants(
    engines: dict[str, Engine],
    database_urls: dict[str, str],
) -> None:
    _record(engines["bot"], _event("stale-grant-fingerprint"))
    error_api_user = os.environ["DB_ERROR_API_USER"].replace('"', '""')

    with engines["migration"].begin() as connection:
        connection.execute(
            text(
                f'GRANT SELECT ON observability.error_occurrences TO "{error_api_user}"'
            )
        )
        connection.execute(
            text(
                "GRANT USAGE, SELECT ON SEQUENCE observability.error_groups_id_seq "
                f'TO "{error_api_user}"'
            )
        )
        connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION observability.record_error("
                "text, text, text, timestamptz, text, text, text, text, text, text, "
                f'text, text, text, bigint[]) TO "{error_api_user}"'
            )
        )

    with engines["error_api"].connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM observability.error_occurrences")
            ).scalar_one()
            == 1
        )
        connection.execute(
            text("SELECT nextval('observability.error_groups_id_seq')")
        ).scalar_one()
        connection.execute(
            text(
                "SELECT observability.record_error("
                "'stale-function-grant', 'x', 'x', CURRENT_TIMESTAMP, 'ERROR', "
                "'x', NULL, NULL, 'x', NULL, NULL, NULL, NULL, ARRAY[]::bigint[])"
            )
        ).scalar_one()

    result = _run_error_api_upgrade(database_urls["migration"])
    assert result.returncode == 0, result.stdout + result.stderr

    _denied(engines["error_api"], "SELECT * FROM observability.error_occurrences")
    _denied(
        engines["error_api"],
        "SELECT nextval('observability.error_groups_id_seq')",
    )
    _denied(
        engines["error_api"],
        "SELECT observability.record_error("
        "'x', 'x', 'x', CURRENT_TIMESTAMP, 'ERROR', 'x', NULL, NULL, "
        "'x', NULL, NULL, NULL, NULL, ARRAY[]::bigint[])",
    )
    with engines["error_api"].connect() as connection:
        group_id = connection.execute(
            text(
                "SELECT id FROM observability.api_error_groups "
                "WHERE fingerprint = 'stale-grant-fingerprint'"
            )
        ).scalar_one()
    with engines["error_api"].begin() as connection:
        connection.execute(
            text(
                "UPDATE observability.error_groups SET status = 'investigating' "
                "WHERE id = :id"
            ),
            {"id": group_id},
        )


def test_bootstrap_rerun_removes_assumable_runtime_memberships(
    engines: dict[str, Engine],
    database_urls: dict[str, str],
    bootstrap_environment: dict[str, str],
) -> None:
    bot_user = bootstrap_environment["DB_APP_USER"].replace('"', '""')
    error_api_user = bootstrap_environment["DB_ERROR_API_USER"].replace('"', '""')
    nested_role = "observability_test_runtime_group"
    with psycopg.connect(
        host=bootstrap_environment["POSTGRES_HOST"],
        port=bootstrap_environment["POSTGRES_PORT"],
        dbname=bootstrap_environment["POSTGRES_DB"],
        user=bootstrap_environment["POSTGRES_USER"],
        password=bootstrap_environment["POSTGRES_PASSWORD"],
        autocommit=True,
    ) as connection:
        connection.execute(f'CREATE ROLE "{nested_role}" NOLOGIN')
        connection.execute(f'GRANT pg_read_all_data TO "{nested_role}"')
        connection.execute(f'GRANT "{nested_role}" TO "{bot_user}"')
        connection.execute(f'GRANT pg_read_all_data TO "{error_api_user}"')

    with engines["bot"].connect() as connection:
        connection.execute(text(f'SET ROLE "{nested_role}"'))
        connection.execute(text("SELECT count(*) FROM observability.error_occurrences"))
    with engines["error_api"].connect() as connection:
        connection.execute(text("SET ROLE pg_read_all_data"))
        connection.execute(text("SELECT count(*) FROM public.telegram_users"))

    _rerun_bootstrap(bootstrap_environment)
    result = _run_error_api_upgrade(database_urls["migration"])
    assert result.returncode == 0, result.stdout + result.stderr

    _denied(engines["bot"], f'SET ROLE "{nested_role}"')
    _denied(engines["error_api"], "SET ROLE pg_read_all_data")
    with psycopg.connect(
        host=bootstrap_environment["POSTGRES_HOST"],
        port=bootstrap_environment["POSTGRES_PORT"],
        dbname=bootstrap_environment["POSTGRES_DB"],
        user=bootstrap_environment["POSTGRES_USER"],
        password=bootstrap_environment["POSTGRES_PASSWORD"],
        autocommit=True,
    ) as connection:
        connection.execute(f'REVOKE pg_read_all_data FROM "{nested_role}"')
        connection.execute(f'DROP ROLE "{nested_role}"')


def test_error_api_migration_repair_removes_global_default_privileges(
    engines: dict[str, Engine],
    database_urls: dict[str, str],
    bootstrap_environment: dict[str, str],
) -> None:
    bot_user = bootstrap_environment["DB_APP_USER"].replace('"', '""')
    error_api_user = bootstrap_environment["DB_ERROR_API_USER"].replace('"', '""')
    with engines["migration"].begin() as connection:
        connection.execute(
            text(
                f'ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO "{error_api_user}"'
            )
        )
        connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES GRANT USAGE ON SEQUENCES TO "
                f'"{error_api_user}"'
            )
        )
        connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO "
                f'"{error_api_user}"'
            )
        )

    _rerun_bootstrap(bootstrap_environment)
    result = _run_error_api_upgrade(database_urls["migration"])
    assert result.returncode == 0, result.stdout + result.stderr

    with engines["migration"].begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE observability.future_default_privilege_test "
                "(id bigint GENERATED BY DEFAULT AS IDENTITY)"
            )
        )
        connection.execute(
            text(
                "CREATE FUNCTION observability.future_default_privilege_test_function() "
                "RETURNS integer LANGUAGE sql AS 'SELECT 1'"
            )
        )
        for grantee in (bot_user, error_api_user):
            assert not connection.execute(
                text(
                    "SELECT has_table_privilege(:grantee, "
                    "'observability.future_default_privilege_test', 'SELECT')"
                ),
                {"grantee": grantee},
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_sequence_privilege(:grantee, "
                    "'observability.future_default_privilege_test_id_seq', 'USAGE')"
                ),
                {"grantee": grantee},
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_function_privilege(:grantee, "
                    "'observability.future_default_privilege_test_function()', 'EXECUTE')"
                ),
                {"grantee": grantee},
            ).scalar_one()

    for engine in (engines["bot"], engines["error_api"]):
        _denied(
            engine,
            "SELECT observability.future_default_privilege_test_function()",
        )
    with engines["migration"].begin() as connection:
        connection.execute(
            text("DROP FUNCTION observability.future_default_privilege_test_function()")
        )
        connection.execute(
            text("DROP TABLE observability.future_default_privilege_test")
        )


def test_error_api_migration_rejects_colliding_runtime_roles(
    engines: dict[str, Engine],
    database_urls: dict[str, str],
    bootstrap_environment: dict[str, str],
) -> None:
    error_api_user = bootstrap_environment["DB_ERROR_API_USER"]
    with _environment(DB_APP_USER=error_api_user, DB_ERROR_API_USER=error_api_user):
        result = _run_error_api_upgrade(database_urls["migration"])

    assert result.returncode != 0
    assert "must name distinct roles" in result.stderr
    _denied(
        engines["error_api"],
        "SELECT observability.record_error("
        "'x', 'x', 'x', CURRENT_TIMESTAMP, 'ERROR', 'x', NULL, NULL, "
        "'x', NULL, NULL, NULL, NULL, ARRAY[]::bigint[])",
    )


def test_error_api_downgrade_preserves_public_data(
    engines: dict[str, Engine],
    database_urls: dict[str, str],
) -> None:
    with engines["migration"].begin() as connection:
        request_id = connection.execute(
            text(
                "INSERT INTO public.media_requests "
                "(url, provider, media_kind, created_at) "
                "VALUES ('https://example.invalid/observability-test', 'test', 'test', "
                "CURRENT_TIMESTAMP) RETURNING id"
            )
        ).scalar_one()
        before = connection.execute(
            text(
                "SELECT count(*) FROM public.media_requests "
                "WHERE url = 'https://example.invalid/observability-test'"
            )
        ).scalar_one()

    with _environment(
        DB_APP_USER=os.environ["DB_APP_USER"],
        DB_ERROR_API_USER=os.environ["DB_ERROR_API_USER"],
    ):
        _downgrade_error_api(database_urls["migration"])

    with engines["migration"].connect() as connection:
        assert (
            connection.execute(
                text("SELECT to_regclass('observability.error_groups')")
            ).scalar_one()
            is None
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM public.media_requests "
                    "WHERE url = 'https://example.invalid/observability-test'"
                )
            ).scalar_one()
            == before
        )

    with engines["migration"].begin() as connection:
        connection.execute(
            text("DELETE FROM public.media_requests WHERE id = :id"),
            {"id": request_id},
        )
