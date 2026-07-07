from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

from ig_reel_downloader.repository.sqlite import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured_url = config.attributes.get("database_url")
    if isinstance(configured_url, str):
        return configured_url

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    db_path = os.getenv("DB_PATH", "data/reels.db")
    return f"sqlite:///{Path(db_path)}"


def _ensure_sqlite_parent(url: str) -> None:
    parsed_url = make_url(url)
    if parsed_url.drivername != "sqlite" or parsed_url.database in (None, ":memory:"):
        return
    Path(parsed_url.database).parent.mkdir(parents=True, exist_ok=True)


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
        return

    database_url = _database_url()
    _ensure_sqlite_parent(database_url)
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
