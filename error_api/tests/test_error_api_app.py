from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import os
import socket
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import ValidationError

from error_api.app import create_app
from error_api.config import ApiSettings
from error_api.repository import postgres as postgres_module
from error_api.repository.postgres import PostgreSQLErrorRepository
from error_api.schemas import (
    ErrorFilters,
    ErrorGroup,
    ErrorGroupPage,
    ErrorNote,
    ErrorOccurrence,
    ErrorPatch,
    NotePage,
    OccurrencePage,
    PageRequest,
    ReproductionCase,
    ReproductionPage,
    encode_cursor,
)

NOW = dt.datetime(2026, 9, 19, tzinfo=dt.UTC)
READ = "read-key-that-is-at-least-thirty-two-characters"
READ_NEXT = "next-read-key-that-is-at-least-thirty-two"
TRIAGE = "triage-key-that-is-at-least-thirty-two-chars"
TRIAGE_NEXT = "next-triage-key-that-is-at-least-thirty-two"


def group(**changes: object) -> ErrorGroup:
    values: dict[str, object] = {
        "reference": "ERR-11",
        "display_name": "Downloader failed",
        "event_code": "download.failed",
        "exception_type": "RuntimeError",
        "status": "new",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "occurrence_count": 1,
        "first_release": "a",
        "last_release": "a",
        "linked_change": None,
        "fixed_at": None,
        "recurred_after_fix": False,
        "first_post_fix_occurrence": None,
    }
    values.update(changes)
    return ErrorGroup.model_validate(values)


class FakeRepository:
    def __init__(self) -> None:
        self.value = group()
        self.note_values: list[ErrorNote] = []
        self.last_filters: ErrorFilters | None = None
        self.last_page: PageRequest | None = None

    def health(self) -> None:
        pass

    def list_groups(self, filters: ErrorFilters, page: PageRequest) -> ErrorGroupPage:
        self.last_filters, self.last_page = filters, page
        return ErrorGroupPage(items=[self.value], next_cursor=None)

    def get_group_for_occurrence(self, occurrence_id: int) -> ErrorGroup | None:
        return self.value if occurrence_id in {1, 2} else None

    def list_occurrences(
        self, occurrence_id: int, page: PageRequest
    ) -> OccurrencePage | None:
        if occurrence_id not in {1, 2}:
            return None
        return OccurrencePage(
            items=[
                ErrorOccurrence(
                    reference="ERR-1",
                    occurred_at=NOW,
                    severity="ERROR",
                    logger_name="safe",
                    component="download",
                    exception_type="RuntimeError",
                    message="safe message",
                    traceback="safe trace",
                    provider="instagram",
                    media_kind="reel",
                    release="a",
                )
            ],
            next_cursor=encode_cursor("occurrences", NOW, 1),
        )

    def list_reproduction_cases(
        self, occurrence_id: int, page: PageRequest
    ) -> ReproductionPage | None:
        if occurrence_id not in {1, 2}:
            return None
        return ReproductionPage(
            items=[
                ReproductionCase(
                    reference="ERR-1",
                    display_name="Downloader failed",
                    occurred_at=NOW,
                    submitted_url="https://example.test/raw",
                    normalized_url="https://example.test/normalized",
                    provider="instagram",
                    media_kind="reel",
                    component="download",
                    release="a",
                )
            ],
            next_cursor=None,
        )

    def list_notes(self, occurrence_id: int, page: PageRequest) -> NotePage | None:
        return (
            NotePage(items=self.note_values, next_cursor=None)
            if occurrence_id in {1, 2}
            else None
        )

    def update_group(self, occurrence_id: int, patch: ErrorPatch) -> ErrorGroup | None:
        if occurrence_id not in {1, 2}:
            return None
        self.value = self.value.model_copy(update=patch.model_dump(exclude_unset=True))
        return self.value

    def add_note(self, occurrence_id: int, note: str, actor: str) -> ErrorNote | None:
        if occurrence_id not in {1, 2}:
            return None
        result = ErrorNote(
            id=len(self.note_values) + 1, note=note, actor=actor, created_at=NOW
        )
        self.note_values.append(result)
        return result


@pytest.fixture
def api() -> tuple[TestClient, FakeRepository]:
    repository = FakeRepository()
    settings = ApiSettings(
        read_key_current=READ,
        read_label_current="reader",
        read_key_next=READ_NEXT,
        read_label_next="reader-next",
        triage_key_current=TRIAGE,
        triage_label_current="operator",
        triage_key_next=TRIAGE_NEXT,
        triage_label_next="operator-next",
    )
    return TestClient(
        create_app(repository, settings), raise_server_exceptions=False
    ), repository


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(
            read_key_current="short",
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        )
    with pytest.raises(ValidationError):
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            read_key_next=READ_NEXT,
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        )
    with pytest.raises(ValidationError):
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            triage_key_current=READ,
            triage_label_current="operator",
        )
    with pytest.raises(ValidationError, match="ASCII"):
        ApiSettings(
            read_key_current="é" * 32,
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        )
    for invalid in ("x" * 31 + " ", "x" * 31 + "\t", "x" * 31 + ":"):
        with pytest.raises(ValidationError, match="bearer"):
            ApiSettings(
                read_key_current=invalid,
                read_label_current="reader",
                triage_key_current=TRIAGE,
                triage_label_current="operator",
            )
    with pytest.raises(ValidationError):
        ApiSettings(
            read_key_current="r" * 4097,
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        )


def test_repository_configures_checkout_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    engine = object()

    def fake_create_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return engine

    monkeypatch.setattr(postgres_module, "create_engine", fake_create_engine)
    repository = PostgreSQLErrorRepository(
        "postgresql+psycopg://api@example.test/errors",
        statement_timeout_ms=1250,
    )

    assert repository._engine is engine  # type: ignore[comparison-overlap]
    assert captured == {
        "url": "postgresql+psycopg://api@example.test/errors",
        "pool_pre_ping": True,
        "pool_timeout": 1.25,
        "connect_args": {
            "connect_timeout": 2,
            "options": "-c statement_timeout=1250",
            "keepalives": 1,
            "keepalives_idle": 2,
            "keepalives_interval": 1,
            "keepalives_count": 3,
            "tcp_user_timeout": 5000,
        },
    }


def test_repository_health_requires_curated_observability_read() -> None:
    statements: list[str] = []

    class Result:
        def all(self) -> list[object]:
            return []

    class Connection:
        def execute(self, statement: object) -> Result:
            statements.append(str(statement))
            return Result()

        def close(self) -> None:
            pass

    class Transaction:
        def commit(self) -> None:
            pass

    repository = object.__new__(PostgreSQLErrorRepository)
    repository._timeout_connection = lambda: (Connection(), Transaction())  # type: ignore[method-assign]

    repository.health()

    assert statements == ["SELECT 1 FROM observability.api_error_groups LIMIT 1"]


def test_authentication_rotation_and_scopes(
    api: tuple[TestClient, FakeRepository],
) -> None:
    client, _ = api
    for headers in ({}, auth("invalid-key-that-is-at-least-thirty-two")):
        response = client.get("/v1/errors", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    response = client.get(
        "/v1/errors",
        headers=[(b"authorization", b"Bearer invalid-\xff-key")],
    )
    assert response.status_code == 401
    assert READ not in response.text
    assert client.get("/openapi.json").status_code == 404
    for key in (READ, READ_NEXT, TRIAGE, TRIAGE_NEXT):
        assert client.get("/v1/health", headers=auth(key)).json() == {"status": "ok"}
    assert (
        client.patch(
            "/v1/errors/ERR-1", headers=auth(READ), json={"status": "resolved"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/errors/ERR-1/notes", headers=auth(READ_NEXT), json={"note": "x"}
        ).status_code
        == 403
    )


def test_authentication_logs_identity_without_disclosing_keys(
    api: tuple[TestClient, FakeRepository],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = api
    invalid = "invalid-key-that-is-at-least-thirty-two"

    with caplog.at_level(logging.INFO, logger="error_api.auth"):
        assert client.get("/v1/health", headers=auth(READ)).status_code == 200
        assert client.get("/v1/health", headers=auth(invalid)).status_code == 401

    read_fingerprint = hashlib.sha256(READ.encode()).hexdigest()[:12]
    invalid_fingerprint = hashlib.sha256(invalid.encode()).hexdigest()[:12]
    assert (
        "credential accepted "
        f"label=reader scope=read fingerprint={read_fingerprint}" in caplog.text
    )
    assert (
        "credential rejected "
        f"reason=unknown fingerprint={invalid_fingerprint}" in caplog.text
    )
    assert READ not in caplog.text
    assert invalid not in caplog.text


def test_dev_authentication_logs_raw_supplied_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = ApiSettings(
        read_key_current=READ,
        read_label_current="reader",
        triage_key_current=TRIAGE,
        triage_label_current="operator",
        log_raw_credentials=True,
    )
    client = TestClient(
        create_app(FakeRepository(), settings), raise_server_exceptions=False
    )
    invalid = "invalid-key-that-is-at-least-thirty-two"

    with caplog.at_level(logging.INFO, logger="error_api.auth"):
        assert client.get("/v1/health", headers=auth(READ)).status_code == 200
        assert client.get("/v1/health", headers=auth(invalid)).status_code == 401

    assert f"credential={READ!r}" in caplog.text
    assert f"credential={invalid!r}" in caplog.text
    assert "fingerprint=" not in caplog.text


def test_all_reads_are_bounded_and_reproduction_is_separate(
    api: tuple[TestClient, FakeRepository],
) -> None:
    client, repository = api
    cursor = encode_cursor("groups", NOW, 7)
    listed = client.get(
        "/v1/errors?status=new&provider=instagram&severity=ERROR"
        "&event_code=download.failed&component=download&release=a"
        f"&limit=1&cursor={cursor}",
        headers=auth(READ),
    )
    assert listed.status_code == 200
    assert repository.last_filters == ErrorFilters(
        status="new",
        provider="instagram",
        severity="ERROR",
        event_code="download.failed",
        component="download",
        release="a",
    )
    assert listed.json()["items"][0]["reference"] == "ERR-11"
    assert repository.last_page == PageRequest(limit=1, cursor_time=NOW, cursor_id=7)
    detail = client.get("/v1/errors/ERR-1", headers=auth(READ)).json()
    assert detail["reference"] == "ERR-11"
    assert "submitted_url" not in str(detail)
    occurrences = client.get("/v1/errors/ERR-1/occurrences", headers=auth(READ)).json()
    assert occurrences["items"][0]["reference"] == "ERR-1"
    assert "submitted_url" not in str(occurrences)
    reproduction = client.get(
        "/v1/errors/ERR-1/reproduction-cases", headers=auth(READ)
    ).json()
    assert reproduction["items"][0]["submitted_url"].endswith("/raw")
    assert client.get("/v1/errors/ERR-1/notes", headers=auth(READ)).status_code == 200
    assert (
        client.get(
            "/v1/errors/ERR-1/occurrences?limit=11", headers=auth(READ)
        ).status_code
        == 422
    )
    assert client.get("/v1/errors?limit=101", headers=auth(READ)).status_code == 422
    assert client.get("/v1/errors?cursor=01", headers=auth(READ)).status_code == 422
    occurrence_cursor = encode_cursor("occurrences", NOW, 7)
    assert (
        client.get(
            f"/v1/errors?cursor={occurrence_cursor}", headers=auth(READ)
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/errors?seen_from=2026-09-20T00:00:00Z&seen_to=2026-09-19T00:00:00Z",
            headers=auth(READ),
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/errors?seen_from=2026-09-19T00:00:00",
            headers=auth(READ),
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("kind", "identifier", "tiebreaker", "expected"),
    [
        ("groups", 9_223_372_036_854_775_807, None, 200),
        ("groups", 9_223_372_036_854_775_808, None, 422),
        ("groups", True, None, 422),
        (
            "reproduction",
            9_223_372_036_854_775_807,
            9_223_372_036_854_775_807,
            200,
        ),
        (
            "reproduction",
            9_223_372_036_854_775_807,
            9_223_372_036_854_775_808,
            422,
        ),
        ("reproduction", 1, True, 422),
    ],
)
def test_cursor_database_domain_is_validated_before_repository_access(
    api: tuple[TestClient, FakeRepository],
    kind: str,
    identifier: int,
    tiebreaker: int | None,
    expected: int,
) -> None:
    client, repository = api
    cursor = encode_cursor(kind, NOW, identifier, tiebreaker)
    path = (
        f"/v1/errors?cursor={cursor}"
        if kind == "groups"
        else f"/v1/errors/ERR-1/reproduction-cases?cursor={cursor}"
    )

    response = client.get(path, headers=auth(READ))

    assert response.status_code == expected
    if expected == 422:
        assert repository.last_page is None


@pytest.mark.parametrize(("key", "expected"), [(None, 401), (READ, 403)])
def test_unauthorized_mutation_is_rejected_without_reading_body(
    key: str | None, expected: int
) -> None:
    repository = FakeRepository()
    application = create_app(
        repository,
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        ),
    )
    headers = (
        [] if key is None else [(b"authorization", f"Bearer {key}".encode("ascii"))]
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/errors/ERR-1/notes",
        "raw_path": b"/v1/errors/ERR-1/notes",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8000),
    }
    sent: list[dict[str, object]] = []

    async def unread() -> dict[str, object]:
        raise AssertionError("unauthorized body was read")

    async def capture(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(application(scope, unread, capture))  # type: ignore[arg-type]

    assert sent[0]["status"] == expected


def test_authenticated_slow_mutation_body_times_out() -> None:
    application = create_app(
        FakeRepository(),
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        ),
        body_read_timeout_seconds=0.01,
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PATCH",
        "scheme": "http",
        "path": "/v1/errors/ERR-1",
        "raw_path": b"/v1/errors/ERR-1",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {TRIAGE}".encode("ascii"))],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8000),
    }
    sent: list[dict[str, object]] = []

    async def stalled() -> dict[str, object]:
        await asyncio.Event().wait()
        raise AssertionError

    async def capture(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(application(scope, stalled, capture))  # type: ignore[arg-type]

    assert sent[0]["status"] == 408


@pytest.mark.parametrize(
    "reference",
    [
        "ERR-0",
        "ERR-01",
        "err-1",
        "ERR-1x",
        " ERR-1",
        "ERR-+1",
        "ERR-9223372036854775808",
        f"ERR-{'9' * 5000}",
    ],
)
def test_reference_is_canonical(
    api: tuple[TestClient, FakeRepository], reference: str
) -> None:
    assert api[0].get(f"/v1/errors/{reference}", headers=auth(READ)).status_code == 404


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("declared", [False, True])
def test_oversized_mutation_bodies_are_rejected_before_parsing(
    api: tuple[TestClient, FakeRepository],
    authenticated: bool,
    declared: bool,
) -> None:
    client, repository = api
    headers = auth(TRIAGE) if authenticated else {}
    body = b'{"note":"' + (b"x" * 40_000) + b'"}'
    if declared:
        headers["Content-Length"] = str(len(body))
        content: object = b"{}"
    else:
        content = iter((body[:20_000], body[20_000:]))

    response = client.post(
        "/v1/errors/ERR-1/notes",
        headers=headers,
        content=content,
    )

    assert response.status_code == (413 if authenticated else 401)
    assert repository.note_values == []


def test_triage_updates_only_group_and_appends_labeled_notes(
    api: tuple[TestClient, FakeRepository],
) -> None:
    client, repository = api
    changed = client.patch(
        "/v1/errors/ERR-1",
        headers=auth(TRIAGE),
        json={
            "display_name": "Investigating downloader",
            "status": "investigating",
            "linked_change": "abc123",
            "fixed_at": NOW.isoformat(),
        },
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "investigating"
    assert (
        client.patch(
            "/v1/errors/ERR-1", headers=auth(TRIAGE), json={"occurrence_count": 0}
        ).status_code
        == 422
    )
    assert (
        client.patch("/v1/errors/ERR-1", headers=auth(TRIAGE), json={}).status_code
        == 422
    )
    for field in ("display_name", "status"):
        assert (
            client.patch(
                "/v1/errors/ERR-1",
                headers=auth(TRIAGE),
                json={field: None},
            ).status_code
            == 422
        )
    cleared = client.patch(
        "/v1/errors/ERR-1",
        headers=auth(TRIAGE),
        json={"linked_change": None, "fixed_at": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["linked_change"] is None
    assert cleared.json()["fixed_at"] is None
    created = client.post(
        "/v1/errors/ERR-1/notes",
        headers=auth(TRIAGE_NEXT),
        json={"note": "Confirmed fix"},
    )
    assert created.status_code == 201
    assert created.json()["actor"] == "operator-next"
    assert repository.note_values[0].note == "Confirmed fix"
    assert (
        client.put(
            "/v1/errors/ERR-1/notes/1", headers=auth(TRIAGE), json={"note": "changed"}
        ).status_code
        == 404
    )
    assert (
        client.delete("/v1/errors/ERR-1/occurrences", headers=auth(TRIAGE)).status_code
        == 405
    )


def test_regression_fields_are_derived_without_workflow_mutation(
    api: tuple[TestClient, FakeRepository],
) -> None:
    client, repository = api
    fixed = NOW - dt.timedelta(seconds=1)
    repository.value = group(
        status="resolved",
        fixed_at=fixed,
        last_seen_at=NOW,
        recurred_after_fix=True,
        first_post_fix_occurrence="ERR-2",
    )
    body = client.get("/v1/errors/ERR-1", headers=auth(READ)).json()
    assert body["recurred_after_fix"] is True
    assert body["first_post_fix_occurrence"] == "ERR-2"
    assert body["status"] == "resolved"
    assert body["fixed_at"] == fixed.isoformat().replace("+00:00", "Z")


def test_internal_errors_and_logs_do_not_disclose_secrets(
    api: tuple[TestClient, FakeRepository], caplog: pytest.LogCaptureFixture
) -> None:
    client, repository = api

    def fail() -> None:
        raise RuntimeError(f"internal SQL Authorization: Bearer {READ}")

    repository.health = fail  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="error_api"):
        response = client.get("/v1/health", headers=auth(READ))
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    combined = response.text + caplog.text
    assert READ not in combined
    assert "Authorization" not in combined
    assert "internal SQL" not in combined


def test_bot_ops_client_against_private_api_http_runtime(
    api: tuple[TestClient, FakeRepository],
) -> None:
    _client, repository = api
    app = create_app(
        repository,
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            triage_key_current=TRIAGE,
            triage_label_current="operator",
        ),
    )
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen()
    port = int(server_socket.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, log_config=None, access_log=False, lifespan="off")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        daemon=True,
    )
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    commands = [
        (["errors", "show", "ERR-1"], READ),
        (["errors", "list", "--since", "24h"], READ),
        (["errors", "mark-fixed", "ERR-1", "--at", "now"], TRIAGE),
    ]
    results: list[subprocess.CompletedProcess[str]] = []
    try:
        for command, key in commands:
            results.append(
                subprocess.run(
                    ["uv", "run", "bot-ops", *command],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env={
                        **os.environ,
                        "ERROR_API_URL": f"http://127.0.0.1:{port}",
                        "ERROR_API_KEY": key,
                    },
                )
            )
    finally:
        server.should_exit = True
        thread.join(timeout=2)
        server_socket.close()

    assert all(result.returncode == 0 for result in results)
    assert "display_name: Downloader failed" in results[0].stdout
    assert "items:" in results[1].stdout
    assert "status: resolved" in results[2].stdout
    assert READ not in "".join(result.stdout + result.stderr for result in results)
    assert TRIAGE not in "".join(result.stdout + result.stderr for result in results)


def test_uvicorn_does_not_relog_handled_repository_details(
    api: tuple[TestClient, FakeRepository],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _client, repository = api
    secret_note = "secret-note-value"
    sql = "INSERT INTO observability.error_notes"
    maximum_key = "t" * 4096

    def fail(_occurrence_id: int, _note: str, _actor: str) -> None:
        raise RuntimeError(f"{sql} bound_note={secret_note}")

    repository.add_note = fail  # type: ignore[method-assign]
    app = create_app(
        repository,
        ApiSettings(
            read_key_current=READ,
            read_label_current="reader",
            triage_key_current=maximum_key,
            triage_label_current="operator",
        ),
    )
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen()
    port = int(server_socket.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, log_config=None, access_log=False, lifespan="off")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        daemon=True,
    )
    with caplog.at_level(logging.ERROR):
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.01)
        try:
            response = httpx.post(
                f"http://127.0.0.1:{port}/v1/errors/ERR-1/notes",
                headers=auth(maximum_key),
                json={"note": secret_note},
                timeout=2,
            )
        finally:
            server.should_exit = True
            thread.join(timeout=2)
            server_socket.close()

    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    combined = response.text + caplog.text
    assert secret_note not in combined
    assert sql not in combined
    assert "RuntimeError" not in combined
