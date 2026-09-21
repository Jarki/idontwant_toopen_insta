"""Independent private Error API process entry point."""

from __future__ import annotations

import logging
import os

import uvicorn
from dotenv import load_dotenv
from pydantic import SecretStr

from .app import create_app
from .config import ApiSettings
from .repository.postgres import PostgreSQLErrorRepository


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is not set")
    return value


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    return value if value is not None and value.strip() else None


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    load_dotenv()
    # Resolve and validate every setting before constructing the server.
    database_url = _required("ERROR_API_DATABASE_URL")
    settings = ApiSettings(
        read_key_current=SecretStr(_required("ERROR_API_READ_KEY")),
        read_label_current=_required("ERROR_API_READ_LABEL"),
        read_key_next=(
            SecretStr(value)
            if (value := _optional("ERROR_API_READ_KEY_NEXT"))
            else None
        ),
        read_label_next=_optional("ERROR_API_READ_LABEL_NEXT"),
        triage_key_current=SecretStr(_required("ERROR_API_TRIAGE_KEY")),
        triage_label_current=_required("ERROR_API_TRIAGE_LABEL"),
        triage_key_next=(
            SecretStr(value)
            if (value := _optional("ERROR_API_TRIAGE_KEY_NEXT"))
            else None
        ),
        triage_label_next=_optional("ERROR_API_TRIAGE_LABEL_NEXT"),
    )
    repository = PostgreSQLErrorRepository(
        database_url,
        statement_timeout_ms=_integer(
            "ERROR_API_STATEMENT_TIMEOUT_MS", 3000, 100, 30_000
        ),
    )
    app = create_app(repository, settings)
    uvicorn.run(
        app,
        host=os.getenv("ERROR_API_HOST", "127.0.0.1"),
        port=_integer("ERROR_API_PORT", 8000, 1, 65535),
        limit_concurrency=32,
        access_log=False,
    )


if __name__ == "__main__":
    main()
