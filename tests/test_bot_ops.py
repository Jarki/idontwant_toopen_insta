from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bot_ops import __main__ as cli
from bot_ops.client import ErrorApiClient, Response, TransportError


@dataclass
class FakeTransport:
    response: Response = field(
        default_factory=lambda: Response(200, b'{"items":[],"next_cursor":null}')
    )
    calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = field(
        default_factory=list
    )

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> Response:
        self.calls.append((method, url, headers, body))
        return self.response


def client(fake: FakeTransport) -> ErrorApiClient:
    return ErrorApiClient("https://api.invalid/root", "top-secret", fake)


def test_all_commands_use_only_published_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeTransport(Response(200, b"{}"))
    api = client(fake)
    monkeypatch.setattr(cli, "_client_from_environment", lambda: api)
    commands = [
        ["errors", "list", "--provider", "x/y?z", "--cursor", "2", "--limit", "1"],
        ["errors", "show", "ERR-1"],
        ["errors", "similar", "ERR-1"],
        ["errors", "repro", "ERR-1"],
        ["errors", "rename", "ERR-1", "new name"],
        ["errors", "status", "ERR-1", "fixing"],
        ["errors", "note", "ERR-1", "checked"],
        ["errors", "link", "ERR-1", "PR-4"],
        ["errors", "mark-fixed", "ERR-1"],
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
    assert "provider=x%2Fy%3Fz" in fake.calls[0][1]
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
        lambda: ErrorApiClient("https://api.invalid", secret, fake),
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
        lambda: ErrorApiClient("https://api.invalid", "secret", Broken()),
    )
    assert cli.main(["errors", "list"]) == cli.EXIT_TRANSPORT


def test_untrusted_terminal_controls_render_inert(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "display_name": "\x1b]2;pwned\x07\n$(touch /tmp/nope)",
        "url": "https://x.invalid/a\rnext",
    }
    fake = FakeTransport(Response(200, json.dumps(payload).encode()))
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


def test_client_forwards_canonical_reference_and_auth_header() -> None:
    fake = FakeTransport(Response(200, b"{}"))
    client(fake).show("ERR-9223372036854775807")
    _, url, headers, _ = fake.calls[0]
    assert url.endswith("/v1/errors/ERR-9223372036854775807")
    assert headers["Authorization"] == "Bearer top-secret"
