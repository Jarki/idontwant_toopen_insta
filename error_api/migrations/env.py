from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from error_api.migrations.runtime_privileges import (
    apply_runtime_privileges,
    validate_runtime_roles,
)
from error_api.repository.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    del name, type_, reflected, compare_to
    return getattr(object_, "schema", None) == "observability"


def _database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if isinstance(configured_url, str) and configured_url:
        return configured_url

    env_url = os.getenv("DB_MIGRATION_URL")
    if env_url:
        return env_url

    msg = "DB_MIGRATION_URL is required for Error API migrations"
    raise RuntimeError(msg)


def _required_role(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    msg = f"{name} is required for Error API migrations"
    raise RuntimeError(msg)


def _runtime_roles() -> tuple[str, str]:
    app_user = _required_role("DB_APP_USER")
    error_api_user = _required_role("DB_ERROR_API_USER")
    validate_runtime_roles(app_user, error_api_user)
    return app_user, error_api_user


def _restrict_runtime_roles(connection: Connection) -> None:
    dialect = getattr(connection, "dialect", None)
    if getattr(dialect, "name", None) != "postgresql":
        return
    app_user, error_api_user = _runtime_roles()
    apply_runtime_privileges(connection, app_user, error_api_user)


def _configure(connection: object | None = None) -> None:
    options: dict[str, object] = {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "version_table": "alembic_version_error_api",
        "version_table_schema": "observability",
        "include_object": _include_object,
    }
    if connection is not None:
        options["connection"] = connection
    else:
        options.update(
            {
                "url": _database_url(),
                "literal_binds": True,
                "dialect_opts": {"paramstyle": "named"},
            }
        )
    context.configure(**options)


def run_migrations_offline() -> None:
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        _configure(existing_connection)
        with context.begin_transaction():
            context.run_migrations()
            _restrict_runtime_roles(existing_connection)
        return

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        with connection.begin():
            _restrict_runtime_roles(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
