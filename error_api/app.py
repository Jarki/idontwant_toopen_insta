"""FastAPI application factory for the private error ledger."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.authentication import AuthenticationMiddleware

from .auth import ApiKeyAuthenticationBackend
from .config import ApiSettings
from .middleware import (
    DEFAULT_BODY_READ_TIMEOUT_SECONDS,
    MAX_MUTATION_BODY_BYTES,
    InternalErrorMiddleware,
    MutationBodyLimitMiddleware,
)
from .repository.base import ErrorRepository
from .routes import router


async def _validation_error(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "invalid request"})


def create_app(
    repository: ErrorRepository,
    settings: ApiSettings,
    *,
    body_read_timeout_seconds: float = DEFAULT_BODY_READ_TIMEOUT_SECONDS,
    max_mutation_body_bytes: int = MAX_MUTATION_BODY_BYTES,
) -> FastAPI:
    """Create the independent HTTP runtime; the bot never imports this module."""

    app = FastAPI(
        title="Private Error API",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.repository = repository
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(router)

    # Starlette applies the most recently added middleware first. Authentication
    # must establish request scopes before the mutation boundary reads a body.
    app.add_middleware(
        MutationBodyLimitMiddleware,
        max_body_bytes=max_mutation_body_bytes,
        body_timeout_seconds=body_read_timeout_seconds,
    )
    app.add_middleware(InternalErrorMiddleware)
    app.add_middleware(
        AuthenticationMiddleware,
        backend=ApiKeyAuthenticationBackend(settings),
    )
    return app
