"""Optional non-mutating smoke test with the provisioned dashboard reader URL."""

import os

import pytest

from dashboard.repository import DashboardRepository


def test_dashboard_reader_snapshot() -> None:
    url = os.getenv("DASHBOARD_TEST_DATABASE_URL")
    if not url:
        pytest.skip("DASHBOARD_TEST_DATABASE_URL is not configured")
    repository = DashboardRepository(url)
    try:
        snapshot = repository.snapshot(7)
        totals = snapshot["totals"]
        assert totals["requests"] == sum(
            totals[key]
            for key in ("delivered", "download_failed", "unconfirmed", "pending")
        )
        assert (
            sum(row["requests"] for row in snapshot["providers"]) == totals["requests"]
        )
        assert sum(row["requests"] for row in snapshot["daily"]) == totals["requests"]
        assert len(snapshot["errors"]) <= 500
        assert all(row["occurrences"] > 0 for row in snapshot["errors"])
    finally:
        repository.engine.dispose()
