"""FastAPI application for bounded private error-ledger access."""

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from .repository.base import ErrorRepository
from .repository.models import (
    ErrorFilters,
    ErrorGroup,
    ErrorGroupPage,
    ErrorNote,
    ErrorPatch,
    NoteCreate,
    NotePage,
    OccurrencePage,
    PageRequest,
    ReproductionPage,
    Severity,
    Status,
)

_LOGGER = logging.getLogger("error_api")
_REFERENCE = re.compile(r"ERR-([1-9][0-9]{0,18})\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*\Z")


class ApiSettings(BaseModel):
    """Fail-closed secrets and non-secret audit labels."""

    model_config = ConfigDict(extra="forbid")
    read_key_current: SecretStr = Field(min_length=32)
    read_label_current: str
    read_key_next: SecretStr | None = Field(default=None, min_length=32)
    read_label_next: str | None = None
    triage_key_current: SecretStr = Field(min_length=32)
    triage_label_current: str
    triage_key_next: SecretStr | None = Field(default=None, min_length=32)
    triage_label_next: str | None = None

    @model_validator(mode="after")
    def validate_credentials(self) -> "ApiSettings":
        pairs = (
            (self.read_key_current, self.read_label_current),
            (self.read_key_next, self.read_label_next),
            (self.triage_key_current, self.triage_label_current),
            (self.triage_key_next, self.triage_label_next),
        )
        secrets: list[str] = []
        for key, label in pairs:
            if (key is None) != (label is None):
                raise ValueError("each configured key requires exactly one label")
            if label is not None and _LABEL.fullmatch(label) is None:
                raise ValueError("credential labels must be non-secret identifiers")
            if key is not None:
                secret = key.get_secret_value()
                try:
                    secret.encode("ascii")
                except UnicodeEncodeError as error:
                    raise ValueError(
                        "API credentials must contain only ASCII"
                    ) from error
                if _BEARER_TOKEN.fullmatch(secret) is None:
                    raise ValueError("API credentials must be valid bearer tokens")
                secrets.append(secret)
        if len(secrets) != len(set(secrets)):
            raise ValueError("API credentials must be distinct")
        return self


@dataclass(frozen=True)
class Principal:
    label: str
    scope: Literal["read", "triage"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"


def parse_reference(reference: str) -> int:
    match = _REFERENCE.fullmatch(reference)
    if match is None:
        raise HTTPException(status_code=404, detail="error not found")
    occurrence_id = int(match.group(1))
    if occurrence_id > 9_223_372_036_854_775_807:
        raise HTTPException(status_code=404, detail="error not found")
    return occurrence_id


def _page(limit: int, cursor: str | None) -> PageRequest:
    if cursor is None:
        return PageRequest(limit=limit)
    if not re.fullmatch(r"0|[1-9][0-9]{0,6}", cursor):
        raise HTTPException(status_code=422, detail="invalid cursor")
    offset = int(cursor)
    if offset > 1_000_000:
        raise HTTPException(status_code=422, detail="invalid cursor")
    return PageRequest(limit=limit, offset=offset)


def create_app(repository: ErrorRepository, settings: ApiSettings) -> FastAPI:
    """Create the independent HTTP runtime; the bot never imports this module."""

    app = FastAPI(
        title="Private Error API",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    configured_values: list[tuple[bytes, Principal]] = [
        (
            hashlib.sha256(
                settings.read_key_current.get_secret_value().encode("ascii")
            ).digest(),
            Principal(settings.read_label_current, "read"),
        ),
        (
            hashlib.sha256(
                settings.triage_key_current.get_secret_value().encode("ascii")
            ).digest(),
            Principal(settings.triage_label_current, "triage"),
        ),
    ]
    if settings.read_key_next is not None and settings.read_label_next is not None:
        configured_values.append(
            (
                hashlib.sha256(
                    settings.read_key_next.get_secret_value().encode("ascii")
                ).digest(),
                Principal(settings.read_label_next, "read"),
            )
        )
    if settings.triage_key_next is not None and settings.triage_label_next is not None:
        configured_values.append(
            (
                hashlib.sha256(
                    settings.triage_key_next.get_secret_value().encode("ascii")
                ).digest(),
                Principal(settings.triage_label_next, "triage"),
            )
        )
    configured = tuple(configured_values)

    def authenticate(
        authorization: Annotated[str | None, Header()] = None,
    ) -> Principal:
        supplied = ""
        well_formed = False
        if authorization is not None and authorization.startswith("Bearer "):
            supplied = authorization[7:]
            well_formed = _BEARER_TOKEN.fullmatch(supplied) is not None
        supplied_digest = hashlib.sha256(supplied.encode("utf-8")).digest()
        matched: Principal | None = None
        # Fixed-length digests avoid text encoding errors and compare every position.
        for secret_digest, principal in configured:
            if hmac.compare_digest(supplied_digest, secret_digest):
                matched = principal
        if not well_formed or matched is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return matched

    def require_triage(
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> Principal:
        if principal.scope != "triage":
            raise HTTPException(status_code=403, detail="insufficient scope")
        return principal

    @app.middleware("http")
    async def contain_internal_errors(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            _LOGGER.error("Error API request failed")
            return JSONResponse(
                status_code=500, content={"detail": "internal server error"}
            )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    @app.get("/v1/health", response_model=HealthResponse)
    def health(
        _principal: Annotated[Principal, Depends(authenticate)],
    ) -> HealthResponse:
        repository.health()
        return HealthResponse()

    @app.get("/v1/errors", response_model=ErrorGroupPage)
    def list_errors(
        _principal: Annotated[Principal, Depends(authenticate)],
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
        return repository.list_groups(filters, _page(limit, cursor))

    @app.get("/v1/errors/{reference}", response_model=ErrorGroup)
    def get_error(
        reference: str, _principal: Annotated[Principal, Depends(authenticate)]
    ) -> ErrorGroup:
        result = repository.get_group_for_occurrence(parse_reference(reference))
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    @app.get("/v1/errors/{reference}/occurrences", response_model=OccurrencePage)
    def occurrences(
        reference: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> OccurrencePage:
        result = repository.list_occurrences(
            parse_reference(reference), _page(limit, cursor)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    @app.get(
        "/v1/errors/{reference}/reproduction-cases", response_model=ReproductionPage
    )
    def reproduction_cases(
        reference: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> ReproductionPage:
        result = repository.list_reproduction_cases(
            parse_reference(reference), _page(limit, cursor)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    @app.get("/v1/errors/{reference}/notes", response_model=NotePage)
    def notes(
        reference: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
    ) -> NotePage:
        result = repository.list_notes(parse_reference(reference), _page(limit, cursor))
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    @app.patch("/v1/errors/{reference}", response_model=ErrorGroup)
    def patch_error(
        reference: str,
        patch: ErrorPatch,
        _principal: Annotated[Principal, Depends(require_triage)],
    ) -> ErrorGroup:
        result = repository.update_group(parse_reference(reference), patch)
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    @app.post("/v1/errors/{reference}/notes", response_model=ErrorNote, status_code=201)
    def create_note(
        reference: str,
        request: NoteCreate,
        principal: Annotated[Principal, Depends(require_triage)],
    ) -> ErrorNote:
        result = repository.add_note(
            parse_reference(reference), request.note, principal.label
        )
        if result is None:
            raise HTTPException(status_code=404, detail="error not found")
        return result

    return app
