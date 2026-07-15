from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ig_reel_downloader.repository.schema import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if isinstance(configured_url, str) and configured_url:
        return configured_url

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    msg = "DATABASE_URL is required for Alembic migrations"
    raise RuntimeError(msg)


def _restrict_app_role(connection: object) -> None:
    dialect = getattr(connection, "dialect", None)
    if getattr(dialect, "name", None) != "postgresql":
        return

    app_user = os.getenv("DB_APP_USER")
    if not app_user:
        msg = "DB_APP_USER is required for PostgreSQL migrations"
        raise RuntimeError(msg)
    quoted_user = dialect.identifier_preparer.quote(app_user)
    for table in ("alembic_version", "reels"):
        connection.exec_driver_sql(
            f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {quoted_user}"
        )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        context.configure(
            connection=existing_connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()
            _restrict_app_role(existing_connection)
        return

    database_url = _database_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()
            _restrict_app_role(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
