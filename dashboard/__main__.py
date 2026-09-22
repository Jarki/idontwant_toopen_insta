"""Run with python -m dashboard; defaults to port 8080."""

import os
from contextlib import suppress

import uvicorn
from dotenv import load_dotenv

from dashboard.app import create_app
from dashboard.repository import DashboardRepository


def main() -> None:
    load_dotenv()
    url = os.getenv("DASHBOARD_DATABASE_URL")
    repository = DashboardRepository(url) if url else None
    try:
        uvicorn.run(
            create_app(repository),
            host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
            port=int(os.getenv("DASHBOARD_PORT", "8080")),
        )
    finally:
        if repository:
            with suppress(Exception):
                repository.engine.dispose()


if __name__ == "__main__":
    main()
