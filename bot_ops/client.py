"""Typed HTTP client for the published private Error API contract."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

_REFERENCE = re.compile(r"ERR-[1-9][0-9]{0,18}\Z")


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


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response: ...


class UrlLibTransport:
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read())
        except (OSError, urllib.error.URLError) as error:
            raise TransportError("could not reach Error API") from error


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
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or UrlLibTransport()

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
        return self._request("GET", "/v1/errors", query=query)

    def show(self, reference: str) -> dict[str, Any]:
        return self._request("GET", self._error_path(reference))

    def similar(
        self, reference: str, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._error_path(reference) + "/occurrences",
            query=self._page(limit, cursor),
        )

    def reproduction_cases(
        self, reference: str, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._error_path(reference) + "/reproduction-cases",
            query=self._page(limit, cursor),
        )

    def rename(self, reference: str, display_name: str) -> dict[str, Any]:
        return self._request(
            "PATCH", self._error_path(reference), payload={"display_name": display_name}
        )

    def set_status(self, reference: str, status: str) -> dict[str, Any]:
        return self._request(
            "PATCH", self._error_path(reference), payload={"status": status}
        )

    def add_note(self, reference: str, note: str) -> dict[str, Any]:
        return self._request(
            "POST", self._error_path(reference) + "/notes", payload={"note": note}
        )

    def link(self, reference: str, change: str | None) -> dict[str, Any]:
        return self._request(
            "PATCH", self._error_path(reference), payload={"linked_change": change}
        )

    def mark_fixed(self, reference: str, fixed_at: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            self._error_path(reference),
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
        if response.status >= 400:
            raise ApiError(f"Error API rejected request (HTTP {response.status})")
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServerError("Error API returned an invalid response") from error
        if not isinstance(decoded, dict):
            raise ServerError("Error API returned an invalid response")
        return cast(dict[str, Any], decoded)
