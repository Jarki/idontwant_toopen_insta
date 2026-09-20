"""Request-scoped dependencies for Error API routes."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from .auth import Principal
from .repository.base import ErrorRepository


def get_error_repository(request: Request) -> ErrorRepository:
    return cast(ErrorRepository, request.app.state.repository)


def get_authenticated_principal(request: Request) -> Principal:
    principal = request.user
    if not isinstance(principal, Principal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def get_triage_principal(
    principal: Annotated[Principal, Depends(get_authenticated_principal)],
) -> Principal:
    if principal.scope != "triage":
        raise HTTPException(status_code=403, detail="insufficient scope")
    return principal
