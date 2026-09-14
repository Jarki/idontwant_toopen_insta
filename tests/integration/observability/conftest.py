from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_URLS = (
    "TEST_DATABASE_ADMIN_URL",
    "TEST_MIGRATION_DATABASE_URL",
    "TEST_APP_DATABASE_URL",
    "TEST_ERROR_API_DATABASE_URL",
)


@dataclass(frozen=True)
class ObservabilityDatabase:
    admin_url: str
    app_url: str
    api_url: str
    main_config: Config
    error_config: Config
    migration_user: str


def _test_url(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is not set; observability integration tests did not run")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+psycopg":
        pytest.fail(f"{name} must use postgresql+psycopg://")
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail(f"{name} must target a database whose name ends in _test")
    return value


def _alembic(
    config_file: str, script: str, database_url: str, **attributes: str
) -> Config:
    config = Config(str(PROJECT_ROOT / config_file))
    config.set_main_option("script_location", str(PROJECT_ROOT / script))
    config.attributes["database_url"] = database_url
    config.attributes.update(attributes)
    return config


@pytest.fixture(scope="session")
def observability_database() -> Iterator[ObservabilityDatabase]:
    urls = {name: _test_url(name) for name in _REQUIRED_URLS}
    admin = make_url(urls["TEST_DATABASE_ADMIN_URL"])
    migration = make_url(urls["TEST_MIGRATION_DATABASE_URL"])
    app = make_url(urls["TEST_APP_DATABASE_URL"])
    api = make_url(urls["TEST_ERROR_API_DATABASE_URL"])
    databases = {url.database for url in (admin, migration, app, api)}
    if len(databases) != 1:
        pytest.fail(
            "All observability test URLs must target the same disposable database"
        )
    if not all((url.username and url.password) for url in (admin, migration, app, api)):
        pytest.fail(
            "All observability test URLs must contain explicit test credentials"
        )

    admin_engine = create_engine(urls["TEST_DATABASE_ADMIN_URL"])
    with admin_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS observability CASCADE"))
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(
            text(f'CREATE SCHEMA public AUTHORIZATION "{admin.username}"')
        )
    admin_engine.dispose()

    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": admin.host or "localhost",
            "POSTGRES_PORT": str(admin.port or 5432),
            "POSTGRES_DB": admin.database or "",
            "POSTGRES_USER": admin.username or "",
            "POSTGRES_PASSWORD": admin.password or "",
            "DB_MIGRATION_USER": migration.username or "",
            "DB_MIGRATION_PASSWORD": migration.password or "",
            "DB_APP_USER": app.username or "",
            "DB_APP_PASSWORD": app.password or "",
            "DB_ERROR_API_USER": api.username or "",
            "DB_ERROR_API_PASSWORD": api.password or "",
        }
    )
    for _ in range(2):
        subprocess.run(
            [sys.executable, "docker/scripts/postgres_bootstrap.py"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
        )

    main_config = _alembic(
        "alembic.ini",
        "migrations",
        urls["TEST_MIGRATION_DATABASE_URL"],
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DB_APP_USER", app.username or "")
        command.upgrade(main_config, "head")

    error_config = _alembic(
        "error_api_alembic.ini",
        "error_api/migrations",
        urls["TEST_MIGRATION_DATABASE_URL"],
        db_app_user=app.username or "",
        db_error_api_user=api.username or "",
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DB_APP_USER", app.username or "")
        monkeypatch.setenv("DB_ERROR_API_USER", api.username or "")
        command.upgrade(error_config, "head")

    # Bootstrap is intentionally rerunnable after both migration streams. It
    # must retain the schema USAGE needed by the two runtime roles without
    # introducing any direct table access.
    subprocess.run(
        [sys.executable, "docker/scripts/postgres_bootstrap.py"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )

    yield ObservabilityDatabase(
        admin_url=urls["TEST_DATABASE_ADMIN_URL"],
        app_url=urls["TEST_APP_DATABASE_URL"],
        api_url=urls["TEST_ERROR_API_DATABASE_URL"],
        main_config=main_config,
        error_config=error_config,
        migration_user=migration.username or "",
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DB_APP_USER", app.username or "")
        monkeypatch.setenv("DB_ERROR_API_USER", api.username or "")
        command.downgrade(error_config, "base")
        command.downgrade(main_config, "base")


@pytest.fixture
def admin_engine(observability_database: ObservabilityDatabase) -> Iterator[Engine]:
    engine = create_engine(observability_database.admin_url)
    yield engine
    engine.dispose()


@pytest.fixture
def app_engine(observability_database: ObservabilityDatabase) -> Iterator[Engine]:
    engine = create_engine(observability_database.app_url)
    yield engine
    engine.dispose()


@pytest.fixture
def api_engine(observability_database: ObservabilityDatabase) -> Iterator[Engine]:
    engine = create_engine(observability_database.api_url)
    yield engine
    engine.dispose()
