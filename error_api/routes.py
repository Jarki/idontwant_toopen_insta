"""HTTP routes for inspecting and triaging recorded errors."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from .dependencies import (
    AuthenticatedPrincipal,
    TriagePrincipal,
    get_repository,
)
from .repository.base import ErrorRepository
from .schemas import (
    ErrorFilters,
    ErrorGroup,
    ErrorGroupPage,
    ErrorNote,
    ErrorPatch,
    HealthResponse,
    NoteCreate,
    NotePage,
    OccurrencePage,
    PageRequest,
    ReproductionPage,
    Severity,
    Status,
    decode_cursor,
)

router = APIRouter(prefix="/v1")
_REFERENCE = re.compile(r"ERR-([1-9][0-9]{0,18})\Z")


def _reference_id(reference: str) -> int:
    match = _REFERENCE.fullmatch(reference)
    if match is None:
        raise HTTPException(status_code=404, detail="error not found")
    occurrence_id = int(match.group(1))
    if occurrence_id > 9_223_372_036_854_775_807:
        raise HTTPException(status_code=404, detail="error not found")
    return occurrence_id


def _page(
    limit: int, cursor: str | None, kind: str, *, tiebreaker: bool = False
) -> PageRequest:
    if cursor is None:
        return PageRequest(limit=limit)
    try:
        when, identifier, tie = decode_cursor(cursor, kind, tiebreaker=tiebreaker)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid cursor") from error
    return PageRequest(
        limit=limit,
        cursor_time=when,
        cursor_id=identifier,
        cursor_tiebreaker=tie,
    )


@router.get("/health", response_model=HealthResponse)
def health(
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
) -> HealthResponse:
    repository.health()
    return HealthResponse()


@router.get("/errors", response_model=ErrorGroupPage)
def list_errors(
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    provider: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    severity: Severity | None = None,
    event_code: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    component: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    release: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    seen_from: AwareDatetime | None = None,
    seen_to: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ErrorGroupPage:
    if seen_from is not None and seen_to is not None and seen_from > seen_to:
        raise HTTPException(status_code=422, detail="invalid seen range")
    filters = ErrorFilters(
        status=status_filter,
        provider=provider,
        severity=severity,
        event_code=event_code,
        component=component,
        release=release,
        seen_from=seen_from,
        seen_to=seen_to,
    )
    return repository.list_groups(filters, _page(limit, cursor, "groups"))


@router.get("/errors/{reference}", response_model=ErrorGroup)
def get_error(
    reference: str,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
) -> ErrorGroup:
    result = repository.get_group_for_occurrence(_reference_id(reference))
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result


@router.get("/errors/{reference}/occurrences", response_model=OccurrencePage)
def occurrences(
    reference: str,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
    cursor: str | None = None,
) -> OccurrencePage:
    result = repository.list_occurrences(
        _reference_id(reference), _page(limit, cursor, "occurrences")
    )
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result


@router.get("/errors/{reference}/reproduction-cases", response_model=ReproductionPage)
def reproduction_cases(
    reference: str,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ReproductionPage:
    result = repository.list_reproduction_cases(
        _reference_id(reference),
        _page(limit, cursor, "reproduction", tiebreaker=True),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result


@router.get("/errors/{reference}/notes", response_model=NotePage)
def notes(
    reference: str,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: AuthenticatedPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> NotePage:
    result = repository.list_notes(
        _reference_id(reference), _page(limit, cursor, "notes")
    )
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result


@router.patch("/errors/{reference}", response_model=ErrorGroup)
def patch_error(
    reference: str,
    patch: ErrorPatch,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    _principal: TriagePrincipal,
) -> ErrorGroup:
    result = repository.update_group(_reference_id(reference), patch)
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result


@router.post("/errors/{reference}/notes", response_model=ErrorNote, status_code=201)
def create_note(
    reference: str,
    request: NoteCreate,
    repository: Annotated[ErrorRepository, Depends(get_repository)],
    principal: TriagePrincipal,
) -> ErrorNote:
    result = repository.add_note(
        _reference_id(reference), request.note, principal.label
    )
    if result is None:
        raise HTTPException(status_code=404, detail="error not found")
    return result
