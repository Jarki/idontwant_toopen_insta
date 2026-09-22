"""Standalone dashboard contract and read-only query coverage."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from dashboard.app import create_app
from dashboard.repository import DashboardRepository


def test_shell_and_missing_database() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "General usage" in response.text
        assert "Error ledger" in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/dashboard").status_code == 503
        assert client.post("/api/dashboard").status_code == 405


def test_snapshot_validation_and_errors() -> None:
    source = MagicMock()
    source.snapshot.return_value = {"totals": {"requests": 4}}
    with TestClient(create_app(source)) as client:
        for days in (0, 91, "invalid"):
            assert client.get(f"/api/dashboard?days={days}").status_code == 422
        source.snapshot.assert_not_called()
        assert client.get("/api/dashboard?days=30").json()["totals"]["requests"] == 4
        source.snapshot.assert_called_once_with(30)
        source.snapshot.side_effect = OperationalError(
            "secret query", {}, Exception("password")
        )
        response = client.get("/api/dashboard")
        assert response.status_code == 503
        assert "password" not in response.text
        assert "secret" not in response.text


def test_repository_readonly_and_bounded() -> None:
    repository = DashboardRepository(
        "postgresql+psycopg://reader:password@localhost/db"
    )
    repository.engine.dispose()
    engine = MagicMock()
    repository.engine = engine
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = [{"requests": 0}]
    result = repository.snapshot(7)
    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert "READ ONLY" in statements[0]
    assert "statement_timeout" in statements[1]
    assert all(sql.lstrip().startswith("SELECT") for sql in statements[3:])
    assert "LIMIT 501" in statements[-1]
    assert result["totals"] == {"requests": 0}
    assert not result["errors_truncated"]


def test_postgresql_required() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        DashboardRepository("sqlite://")


def test_error_group_cap_and_window_parameters() -> None:
    repository = DashboardRepository(
        "postgresql+psycopg://reader:password@localhost/db"
    )
    repository.engine.dispose()
    repository.engine = MagicMock()
    connection = repository.engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.side_effect = [
        [{"requests": 0}],
        [],
        [],
        [],
        [{"id": n} for n in range(501)],
    ]
    result = repository.snapshot(30)
    assert result["errors_truncated"]
    assert len(result["errors"]) == 500
    query_calls = connection.execute.call_args_list[3:]
    request_since = query_calls[0].args[1]["since"]
    error_since = query_calls[-1].args[1]["since"]
    assert request_since.tzinfo is None
    assert error_since.tzinfo is not None
    assert request_since == error_since.replace(tzinfo=None)
