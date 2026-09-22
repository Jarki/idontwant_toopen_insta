"""Standalone HTTP surface. It does not import or call the Error API runtime."""

import logging
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError


class SnapshotSource(Protocol):
    def snapshot(self, days: int) -> dict[str, Any]: ...


def create_app(source: SnapshotSource | None = None) -> FastAPI:
    app = FastAPI(
        title="Bot dashboard", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def security_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
        )
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(Path(__file__).with_name("index.html"))

    @app.get("/api/dashboard")
    def snapshot(days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
        if source is None:
            raise HTTPException(
                503, "Database not configured. Set DASHBOARD_DATABASE_URL and restart."
            )
        try:
            return source.snapshot(days)
        except SQLAlchemyError:
            logging.getLogger(__name__).warning("Dashboard database query failed")
            raise HTTPException(
                503,
                "Dashboard data is unavailable. Check database connectivity and read permissions.",
            ) from None

    return app
