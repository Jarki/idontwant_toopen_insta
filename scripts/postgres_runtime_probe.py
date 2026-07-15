#!/usr/bin/env python3
"""One-shot application-role probe used by the PostgreSQL Compose test."""

import os

from sqlalchemy import create_engine, make_url, text

RUNTIME_TABLES = ("media_items", "media_assets", "judgmental_animations")


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or not database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must use postgresql+psycopg://")

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            actual_user = connection.exec_driver_sql("SELECT current_user").scalar_one()
            expected_user = make_url(database_url).username
            if actual_user != expected_user:
                raise RuntimeError(
                    f"Connected as {actual_user!r}, expected {expected_user!r}"
                )
            for table in RUNTIME_TABLES:
                connection.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
    finally:
        engine.dispose()

    print("PostgreSQL application-role runtime probe passed")


if __name__ == "__main__":
    main()
