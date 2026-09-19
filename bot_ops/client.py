"""Typed HTTP client for the published private Error API contract."""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

_REFERENCE = re.compile(r"ERR-[1-9][0-9]{0,18}\Z")
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*\Z")
_MAX_KEY_BYTES = 4096
_MAX_RESPONSE_BYTES = 1_048_576
_EXCHANGE_SECONDS = 10.0
Status = Literal["new", "investigating", "fixing", "monitoring", "resolved", "ignored"]
Severity = Literal["ERROR", "CRITICAL"]


class ClientError(Exception):
    """A safe, categorized client failure."""


class AuthenticationError(ClientError):
    pass


class AuthorizationError(ClientError):
    pass


class TransportError(ClientError):
    pass


class ServerError(ClientError):
    pass


class ApiError(ClientError):
    pass


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ErrorGroup(ContractModel):
    id: int
    display_name: str
    event_code: str
    exception_type: str | None
    status: Status
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    occurrence_count: int
    first_release: str | None
    last_release: str | None
    linked_change: str | None
    fixed_at: AwareDatetime | None
    recurred_after_fix: bool
    first_post_fix_occurrence: str | None = None


class ErrorOccurrence(ContractModel):
    reference: str
    occurred_at: AwareDatetime
    severity: Severity
    logger_name: str
    component: str | None
    exception_type: str | None
    message: str
    traceback: str | None
    provider: str | None
    media_kind: str | None
    release: str | None


class ReproductionCase(ContractModel):
    reference: str
    display_name: str
    occurred_at: AwareDatetime
    submitted_url: str | None
    normalized_url: str | None
    provider: str | None
    media_kind: str | None
    component: str | None
    release: str | None


class ErrorNote(ContractModel):
    id: int
    note: str
    actor: str
    created_at: AwareDatetime


class ErrorGroupPage(ContractModel):
    items: list[ErrorGroup]
    next_cursor: str | None


class OccurrencePage(ContractModel):
    items: list[ErrorOccurrence]
    next_cursor: str | None


class ReproductionPage(ContractModel):
    items: list[ReproductionCase]
    next_cursor: str | None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response: ...


class HttpTransport:
    """Single-exchange transport: no redirects, bounded bytes, total deadline."""

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname is None:
            raise TransportError("could not complete Error API exchange")
        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        deadline = time.monotonic() + _EXCHANGE_SECONDS
        connection = connection_type(
            parsed.hostname, parsed.port, timeout=_EXCHANGE_SECONDS
        )
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("Error API exchange timed out")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(65_536, _MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ServerError("Error API response exceeded size limit")
            return Response(status=response.status, body=b"".join(chunks))
        except (TimeoutError, http.client.HTTPException, OSError, ValueError) as error:
            raise TransportError("could not complete Error API exchange") from error
        finally:
            connection.close()


_Model = TypeVar("_Model", bound=ContractModel)


class ErrorApiClient:
    """Narrow client exposing only documented v1 error-ledger operations."""

    def __init__(self, base_url: str, api_key: str, transport: Transport | None = None):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ERROR_API_URL must be an http(s) URL")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError(
                "ERROR_API_URL must not contain credentials, query, or fragment"
            )
        try:
            key_bytes = api_key.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("ERROR_API_KEY is invalid") from error
        if (
            not 1 <= len(key_bytes) <= _MAX_KEY_BYTES
            or _BEARER_TOKEN.fullmatch(api_key) is None
        ):
            raise ValueError("ERROR_API_KEY is invalid")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or HttpTransport()

    def list_errors(self, filters: Mapping[str, str | int | None]) -> dict[str, Any]:
        allowed = {
            "status",
            "provider",
            "severity",
            "event_code",
            "component",
            "release",
            "seen_from",
            "seen_to",
            "limit",
            "cursor",
        }
        query = [
            (key, str(value))
            for key, value in filters.items()
            if key in allowed and value is not None
        ]
        return self._request("GET", "/v1/errors", ErrorGroupPage, query=query)

    def show(self, reference: str) -> dict[str, Any]:
        return self._request("GET", self._error_path(reference), ErrorGroup)

    def similar(
        self, reference: str, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._error_path(reference) + "/occurrences",
            OccurrencePage,
            query=self._page(limit, cursor),
        )

    def reproduction_cases(
        self, reference: str, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._error_path(reference) + "/reproduction-cases",
            ReproductionPage,
            query=self._page(limit, cursor),
        )

    def rename(self, reference: str, display_name: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            self._error_path(reference),
            ErrorGroup,
            payload={"display_name": display_name},
        )

    def set_status(self, reference: str, status: str) -> dict[str, Any]:
        return self._request(
            "PATCH", self._error_path(reference), ErrorGroup, payload={"status": status}
        )

    def add_note(self, reference: str, note: str) -> dict[str, Any]:
        return self._request(
            "POST",
            self._error_path(reference) + "/notes",
            ErrorNote,
            payload={"note": note},
        )

    def link(self, reference: str, change: str | None) -> dict[str, Any]:
        return self._request(
            "PATCH",
            self._error_path(reference),
            ErrorGroup,
            payload={"linked_change": change},
        )

    def mark_fixed(self, reference: str, fixed_at: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            self._error_path(reference),
            ErrorGroup,
            payload={"status": "resolved", "fixed_at": fixed_at},
        )

    @staticmethod
    def _page(limit: int, cursor: str | None) -> list[tuple[str, str]]:
        values = [("limit", str(limit))]
        if cursor is not None:
            values.append(("cursor", cursor))
        return values

    @staticmethod
    def _error_path(reference: str) -> str:
        if _REFERENCE.fullmatch(reference) is None:
            raise ApiError("invalid error reference")
        return "/v1/errors/" + reference

    def _request(
        self,
        method: str,
        path: str,
        model: type[_Model],
        *,
        query: list[tuple[str, str]] | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        url = self._base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode()
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self._transport.request(method, url, headers, body)
        if response.status == 401:
            raise AuthenticationError("Error API authentication failed")
        if response.status == 403:
            raise AuthorizationError("Error API authorization failed")
        if response.status >= 500:
            raise ServerError("Error API server failure")
        if response.status >= 400 or not 200 <= response.status < 300:
            raise ApiError(f"Error API rejected request (HTTP {response.status})")
        try:
            decoded = model.model_validate_json(response.body)
        except ValidationError as error:
            raise ServerError("Error API returned an invalid response") from error
        return decoded.model_dump(mode="json")
