from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from error_api.repository.schema import ErrorApiBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ErrorApiBase.metadata


def _database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if isinstance(configured_url, str) and configured_url:
        return configured_url
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    raise RuntimeError("DATABASE_URL is required for Error API Alembic migrations")


def _configure_roles() -> None:
    app_user = config.attributes.get("db_app_user") or os.getenv("DB_APP_USER")
    api_user = config.attributes.get("db_error_api_user") or os.getenv(
        "DB_ERROR_API_USER"
    )
    if not app_user or not api_user:
        raise RuntimeError(
            "DB_APP_USER and DB_ERROR_API_USER are required for Error API migrations"
        )
    config.attributes["db_app_user"] = app_user
    config.attributes["db_error_api_user"] = api_user


def run_migrations_offline() -> None:
    _configure_roles()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        version_table="alembic_version_error_api",
        version_table_schema="observability",
        include_schemas=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _configure_roles()
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        context.configure(
            connection=existing_connection,
            target_metadata=target_metadata,
            version_table="alembic_version_error_api",
            version_table_schema="observability",
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_error_api",
            version_table_schema="observability",
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
