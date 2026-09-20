from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bot_ops import __main__ as cli, client as client_module
from bot_ops.client import (
    ApiError,
    ErrorApiClient,
    Response,
    ServerError,
    TransportError,
)

KEY = "k" * 32


def group_payload(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "reference": "ERR-11",
        "display_name": "Downloader failed",
        "event_code": "download.failed",
        "exception_type": "RuntimeError",
        "status": "new",
        "first_seen_at": "2026-09-19T00:00:00Z",
        "last_seen_at": "2026-09-19T00:00:00Z",
        "occurrence_count": 1,
        "first_release": "a",
        "last_release": "a",
        "linked_change": None,
        "fixed_at": None,
        "recurred_after_fix": False,
        "first_post_fix_occurrence": None,
    }
    result.update(changes)
    return result


def encoded(value: object) -> bytes:
    return json.dumps(value).encode()


@dataclass
class FakeTransport:
    response: Response | None = None
    calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = field(
        default_factory=list
    )

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        self.calls.append((method, url, headers, body))
        if self.response is not None:
            return self.response
        if method == "POST":
            return Response(
                201,
                encoded(
                    {
                        "id": 1,
                        "note": "checked",
                        "actor": "operator",
                        "created_at": "2026-09-19T00:00:00Z",
                    }
                ),
            )
        if url.endswith("/occurrences?limit=10"):
            return Response(200, encoded({"items": [], "next_cursor": None}))
        if url.endswith("/reproduction-cases?limit=50"):
            return Response(200, encoded({"items": [], "next_cursor": None}))
        if "/v1/errors?" in url:
            return Response(200, encoded({"items": [], "next_cursor": None}))
        return Response(200, encoded(group_payload()))


def client(fake: FakeTransport) -> ErrorApiClient:
    return ErrorApiClient("https://api.invalid/root", KEY, fake)


def test_all_commands_use_only_published_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeTransport()
    api = client(fake)
    monkeypatch.setattr(cli, "_client_from_environment", lambda: api)
    commands = [
        [
            "errors",
            "list",
            "--provider",
            "x/y?z",
            "--cursor",
            "2",
            "--limit",
            "1",
            "--since",
            "24h",
        ],
        ["errors", "show", "ERR-1"],
        ["errors", "similar", "ERR-1"],
        ["errors", "repro", "ERR-1"],
        ["errors", "rename", "ERR-1", "new name"],
        ["errors", "status", "ERR-1", "fixing"],
        ["errors", "note", "ERR-1", "checked"],
        ["errors", "link", "ERR-1", "PR-4"],
        ["errors", "mark-fixed", "ERR-1", "--at", "now"],
    ]
    for command in commands:
        assert cli.main(command) == 0
    assert [(method, url.split("?", 1)[0]) for method, url, _, _ in fake.calls] == [
        ("GET", "https://api.invalid/root/v1/errors"),
        ("GET", "https://api.invalid/root/v1/errors/ERR-1"),
        ("GET", "https://api.invalid/root/v1/errors/ERR-1/occurrences"),
        ("GET", "https://api.invalid/root/v1/errors/ERR-1/reproduction-cases"),
        ("PATCH", "https://api.invalid/root/v1/errors/ERR-1"),
        ("PATCH", "https://api.invalid/root/v1/errors/ERR-1"),
        ("POST", "https://api.invalid/root/v1/errors/ERR-1/notes"),
        ("PATCH", "https://api.invalid/root/v1/errors/ERR-1"),
        ("PATCH", "https://api.invalid/root/v1/errors/ERR-1"),
    ]
    assert "seen_from=" in fake.calls[0][1]
    assert "since=" not in fake.calls[0][1]
    assert "provider=x%2Fy%3Fz" in fake.calls[0][1]
    assert "fixed_at" in json.loads(fake.calls[-1][3] or b"")
    assert json.loads(fake.calls[-1][3] or b"")["status"] == "resolved"
    capsys.readouterr()


def test_reference_cannot_inject_path() -> None:
    fake = FakeTransport()
    with pytest.raises(Exception, match="invalid error reference"):
        client(fake).show("ERR-1/notes?limit=100")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, cli.EXIT_AUTH),
        (403, cli.EXIT_AUTHZ),
        (422, cli.EXIT_API),
        (500, cli.EXIT_SERVER),
    ],
)
def test_http_failure_categories(
    status: int,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "never-print-this"
    fake = FakeTransport(Response(status, f'{{"detail":"{secret}"}}'.encode()))
    monkeypatch.setattr(
        cli,
        "_client_from_environment",
        lambda: ErrorApiClient("https://api.invalid", KEY, fake),
    )
    assert cli.main(["errors", "show", "ERR-1"]) == expected
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_config_and_transport_have_distinct_secret_free_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ERROR_API_URL", raising=False)
    monkeypatch.setenv("ERROR_API_KEY", "secret-value")
    assert cli.main(["errors", "list"]) == cli.EXIT_CONFIG
    assert "secret-value" not in capsys.readouterr().err

    class Broken:
        def request(
            self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
        ) -> Response:
            raise TransportError("could not reach Error API")

    monkeypatch.setattr(
        cli,
        "_client_from_environment",
        lambda: ErrorApiClient("https://api.invalid", KEY, Broken()),
    )
    assert cli.main(["errors", "list"]) == cli.EXIT_TRANSPORT


def test_untrusted_terminal_controls_render_inert(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = group_payload(
        display_name="\x1b]2;pwned\x07\n$(touch /tmp/nope)",
        event_code="https://x.invalid/a\rnext",
    )
    fake = FakeTransport(Response(200, encoded(payload)))
    monkeypatch.setattr(cli, "_client_from_environment", lambda: client(fake))
    assert cli.main(["errors", "show", "ERR-1"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output and "\x07" not in output and "\rnext" not in output
    assert (
        "\\x1b" in output and "\\u0007" in output and "\\n$(touch /tmp/nope)" in output
    )
    assert not Path("/tmp/nope").exists()


def test_environment_is_only_configuration_and_entrypoint_is_installed() -> None:
    result = subprocess.run(
        ["uv", "run", "bot-ops", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0
    assert "errors" in result.stdout


def test_import_boundary_and_no_database_configuration() -> None:
    package = Path(__file__).parent.parent / "bot_ops"
    content = "".join(
        (package / name).read_text(encoding="utf-8")
        for name in ("__init__.py", "__main__.py", "client.py")
    )
    forbidden = (
        "ig_reel_downloader",
        "error_api",
        "sqlalchemy",
        "DATABASE_URL",
        "subprocess",
        "os.system",
    )
    assert all(value not in content for value in forbidden)
    assert "--database" not in cli._parser().format_help()


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.test",
        "http://localhost:8000",
        "http://192.168.1.10:8000",
    ],
)
def test_cleartext_transport_is_rejected_for_nonliteral_loopback(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ErrorApiClient(url, KEY, FakeTransport())


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "http://127.25.1.2:8000", "http://[::1]:8000"],
)
def test_cleartext_transport_allows_only_literal_loopback(url: str) -> None:
    ErrorApiClient(url, KEY, FakeTransport())


def test_maximum_occurrence_page_fits_client_response_contract() -> None:
    occurrence = {
        "reference": "ERR-1",
        "occurred_at": "2026-09-19T00:00:00Z",
        "severity": "ERROR",
        "logger_name": "provider",
        "component": "download",
        "exception_type": "RuntimeError",
        "message": "\u0000" * 8192,
        "traceback": "\u0000" * 65536,
        "provider": "instagram",
        "media_kind": "reel",
        "release": "a",
    }
    body = encoded({"items": [occurrence] * 10, "next_cursor": None})
    fake = FakeTransport(Response(200, body))

    result = client(fake).similar("ERR-1", limit=10, cursor=None)

    assert len(body) <= client_module._MAX_RESPONSE_BYTES
    assert len(result["items"]) == 10


def test_client_forwards_canonical_reference_and_auth_header() -> None:
    fake = FakeTransport(Response(200, encoded(group_payload())))
    client(fake).show("ERR-9223372036854775807")
    _, url, headers, _ = fake.calls[0]
    assert url.endswith("/v1/errors/ERR-9223372036854775807")
    assert headers["Authorization"] == f"Bearer {KEY}"


class QuietServer(socketserver.TCPServer):
    allow_reuse_address = True


def _serve(
    handler: type[http.server.BaseHTTPRequestHandler],
) -> tuple[QuietServer, threading.Thread, str]:
    server = QuietServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def _stop(server: QuietServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_redirect_is_rejected_without_forwarding_authorization() -> None:
    received: list[str | None] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    target, target_thread, target_url = _serve(Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", target_url + "/stolen")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    redirect, redirect_thread, redirect_url = _serve(Redirect)
    try:
        with pytest.raises(ApiError, match="HTTP 302"):
            ErrorApiClient(redirect_url, KEY).show("ERR-1")
        time.sleep(0.05)
    finally:
        _stop(redirect, redirect_thread)
        _stop(target, target_thread)
    assert received == []


def test_oversized_and_trickle_responses_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Oversized(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (client_module._MAX_RESPONSE_BYTES + 1))

        def log_message(self, format: str, *args: object) -> None:
            pass

    server, thread, url = _serve(Oversized)
    try:
        with pytest.raises(ServerError, match="size limit"):
            ErrorApiClient(url, KEY).show("ERR-1")
    finally:
        _stop(server, thread)

    monkeypatch.setattr(client_module, "_EXCHANGE_SECONDS", 0.1)

    class Trickle(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.03)
            except BrokenPipeError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server, thread, url = _serve(Trickle)
    started = time.monotonic()
    try:
        with pytest.raises(TransportError):
            ErrorApiClient(url, KEY).show("ERR-1")
        elapsed = time.monotonic() - started
    finally:
        _stop(server, thread)
    assert elapsed < 1


def test_header_trickle_obeys_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_EXCHANGE_SECONDS", 0.1)

    class HeaderTrickle(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                self.wfile.flush()
                for index in range(20):
                    self.wfile.write(f"X-Pad-{index}: x\r\n".encode())
                    self.wfile.flush()
                    time.sleep(0.03)
                self.wfile.write(b"\r\n{}")
            except BrokenPipeError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server, thread, url = _serve(HeaderTrickle)
    started = time.monotonic()
    try:
        with pytest.raises(TransportError):
            ErrorApiClient(url, KEY).show("ERR-1")
        elapsed = time.monotonic() - started
    finally:
        _stop(server, thread)
    assert elapsed < 0.3


@pytest.mark.parametrize(
    "key",
    ["bad\nsecret", "bad\rsecret", "bad key", "é", "x" * 31, "x" * 4097],
)
def test_malformed_api_keys_fail_as_fixed_secret_free_configuration(
    key: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ERROR_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ERROR_API_KEY", key)
    assert cli.main(["errors", "show", "ERR-1"]) == cli.EXIT_CONFIG
    output = capsys.readouterr().err
    assert output == "configuration error: invalid Error API configuration\n"
    assert key not in output


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://:80",
        "http:///missing-host",
    ],
)
def test_invalid_url_ports_are_fixed_configuration_errors(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ERROR_API_URL", url)
    monkeypatch.setenv("ERROR_API_KEY", KEY)
    assert cli.main(["errors", "show", "ERR-1"]) == cli.EXIT_CONFIG
    assert capsys.readouterr().err == (
        "configuration error: invalid Error API configuration\n"
    )


@pytest.mark.parametrize("value", ["0h", "1s", "366d", "10000m", "forever"])
def test_since_rejects_unbounded_or_malformed_durations(value: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args(["errors", "list", "--since", value])
    assert error.value.code == cli.EXIT_CONFIG


def test_blocked_resolver_obeys_deadline_without_lingering_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_EXCHANGE_SECONDS", 0.1)

    def blocked_resolver(
        host: str,
        port: int,
        type: int,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        time.sleep(10)
        return []

    monkeypatch.setattr(client_module.socket, "getaddrinfo", blocked_resolver)
    started = time.monotonic()
    with pytest.raises(TransportError):
        ErrorApiClient("https://blocked.invalid", KEY).show("ERR-1")
    assert time.monotonic() - started < 0.5
    assert not client_module.multiprocessing.active_children()


def test_installed_cli_resolver_deadline(
    tmp_path: Path,
) -> None:
    (tmp_path / "sitecustomize.py").write_text(
        "import time\n"
        "import bot_ops.client as client\n"
        "client._EXCHANGE_SECONDS = 0.1\n"
        "def blocked(*args, **kwargs):\n"
        "    time.sleep(10)\n"
        "    return []\n"
        "client.socket.getaddrinfo = blocked\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = subprocess.run(
        ["uv", "run", "bot-ops", "errors", "show", "ERR-1"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path),
            "ERROR_API_URL": "https://blocked.invalid",
            "ERROR_API_KEY": KEY,
        },
    )
    assert time.monotonic() - started < 1.5
    assert result.returncode == cli.EXIT_TRANSPORT
    assert KEY not in result.stdout + result.stderr


def test_resolver_resource_exhaustion_is_safe_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def exhausted_pipe(*, duplex: bool) -> object:
        raise OSError("secret resource detail")

    monkeypatch.setattr(client_module.multiprocessing, "Pipe", exhausted_pipe)
    monkeypatch.setattr(
        cli,
        "_client_from_environment",
        lambda: ErrorApiClient("https://api.invalid", KEY),
    )
    assert cli.main(["errors", "show", "ERR-1"]) == cli.EXIT_TRANSPORT
    assert "secret resource detail" not in capsys.readouterr().err


def test_resolver_start_failure_closes_partial_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Endpoint:
        closed = False

        def close(self) -> None:
            self.closed = True

    receiver = Endpoint()
    sender = Endpoint()

    class FailedProcess:
        pid = None
        closed = False

        def __init__(self, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise OSError("no process slots")

        def close(self) -> None:
            self.closed = True

    process = FailedProcess()

    def pipe_factory(*, duplex: bool) -> tuple[Endpoint, Endpoint]:
        assert duplex is False
        return receiver, sender

    def process_factory(**kwargs: object) -> FailedProcess:
        assert kwargs["daemon"] is True
        return process

    monkeypatch.setattr(client_module.multiprocessing, "Pipe", pipe_factory)
    monkeypatch.setattr(client_module.multiprocessing, "Process", process_factory)
    with pytest.raises(TransportError):
        client_module._resolve_addresses("api.invalid", 80, 0.1)
    assert receiver.closed
    assert sender.closed
    assert process.closed


@pytest.mark.parametrize(
    "error",
    [
        http.client.BadStatusLine("secret"),
        http.client.LineTooLong("secret"),
        http.client.IncompleteRead(b"secret"),
    ],
)
def test_protocol_failures_are_secret_free_transport_errors(
    error: http.client.HTTPException,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ProtocolFailure:
        sock = None

        def __init__(self, host: str, port: int | None, timeout: float) -> None:
            pass

        def request(
            self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]
        ) -> None:
            pass

        def getresponse(self) -> object:
            raise error

        def close(self) -> None:
            pass

    monkeypatch.setattr(client_module.http.client, "HTTPConnection", ProtocolFailure)
    monkeypatch.setattr(
        cli,
        "_client_from_environment",
        lambda: ErrorApiClient("https://api.invalid", KEY),
    )
    assert cli.main(["errors", "show", "ERR-1"]) == cli.EXIT_TRANSPORT
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"detail": "failure"},
        {"items": "wrong", "next_cursor": None},
        {**group_payload(), "unexpected": True},
        {**group_payload(), "occurrence_count": "one"},
    ],
)
def test_invalid_success_contract_is_server_failure(payload: object) -> None:
    fake = FakeTransport(Response(200, encoded(payload)))
    with pytest.raises(ServerError, match="invalid response"):
        client(fake).show("ERR-1")
