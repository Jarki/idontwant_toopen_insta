from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = PROJECT_ROOT / "docker/compose.yaml"


def _environment() -> dict[str, str]:
    return {
        "BOT_TOKEN": "telegram-token",
        "APP_RELEASE": "test-release",
        "POSTGRES_DB": "reels",
        "POSTGRES_USER": "db_owner",
        "POSTGRES_PASSWORD": "owner-secret",
        "DB_MIGRATION_USER": "db_migration",
        "DB_MIGRATION_PASSWORD": "migration-secret",
        "DB_APP_USER": "db_app",
        "DB_APP_PASSWORD": "app-secret",
        "DB_ERROR_API_USER": "db_error_api",
        "DB_ERROR_API_PASSWORD": "api-db-secret",
        "DB_MIGRATION_URL": "postgresql+psycopg://db_migration:migration-secret@postgres:5432/reels",
        "DATABASE_URL": "postgresql+psycopg://db_app:app-secret@postgres:5432/reels",
        "ERROR_API_DATABASE_URL": "postgresql+psycopg://db_error_api:api-db-secret@postgres:5432/reels",
        "ERROR_API_READ_KEY": "r" * 32,
        "ERROR_API_READ_LABEL": "reader",
        "ERROR_API_TRIAGE_KEY": "t" * 32,
        "ERROR_API_TRIAGE_LABEL": "operator",
    }


def _compose_config(overlay: str | None = None) -> dict[str, Any]:
    command = ["docker", "compose", "-f", str(BASE_COMPOSE)]
    if overlay is not None:
        command.extend(["-f", str(PROJECT_ROOT / overlay)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env={**os.environ, **_environment()},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_compose_enforces_migration_gates_and_private_network_surface() -> None:
    config = _compose_config()
    services = config["services"]

    assert "ports" not in services["postgres"]
    assert services["postgres-bootstrap"]["depends_on"] == {
        "postgres": {"condition": "service_healthy", "required": True}
    }
    assert services["migrate"]["depends_on"] == {
        "postgres-bootstrap": {
            "condition": "service_completed_successfully",
            "required": True,
        }
    }
    assert services["error-api-migrate"]["depends_on"] == {
        "migrate": {"condition": "service_completed_successfully", "required": True}
    }
    gate = {
        "error-api-migrate": {
            "condition": "service_completed_successfully",
            "required": True,
        }
    }
    assert services["downloader"]["depends_on"] == gate
    assert services["error-api"]["depends_on"] == gate
    assert services["error-api"]["ports"] == [
        {
            "mode": "ingress",
            "target": 8000,
            "published": "8000",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    assert services["error-api"]["entrypoint"] == [
        "/app/.venv/bin/python",
        "-m",
        "error_api",
    ]
    assert services["error-api-migrate"]["command"] == [
        "-c",
        "error_api_alembic.ini",
        "upgrade",
        "head",
    ]
    assert "DB_MIGRATION_URL" not in services["downloader"]["environment"]
    assert "ERROR_API_READ_KEY" not in services["downloader"]["environment"]
    assert "BOT_TOKEN" not in services["error-api"]["environment"]


@pytest.mark.parametrize(
    ("overlay", "tag"),
    [
        ("docker/compose.dev.yaml", "latest"),
        ("docker/compose.prod.yaml", "prod"),
    ],
)
def test_compose_overlays_use_one_image_for_every_application_service(
    overlay: str, tag: str
) -> None:
    services = _compose_config(overlay)["services"]
    expected = f"ghcr.io/jarki/idontwant_toopen_insta:{tag}"
    for name in (
        "postgres-bootstrap",
        "migrate",
        "error-api-migrate",
        "downloader",
        "error-api",
    ):
        assert services[name]["image"] == expected
        assert "build" not in services[name]


def _run_preflight(
    tmp_path: Path, values: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{name}='{value}'\n" for name, value in values.items()),
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(PROJECT_ROOT / "docker/preflight.sh"), str(tmp_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_preflight_accepts_consistent_configuration(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, _environment())

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight PASSED" in result.stdout


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values.pop("ERROR_API_TRIAGE_KEY"),
        lambda values: values.__setitem__("DB_ERROR_API_USER", "db_app"),
        lambda values: values.__setitem__(
            "ERROR_API_DATABASE_URL",
            "postgresql+psycopg://db_error_api:wrong-secret@postgres:5432/reels",
        ),
        lambda values: values.__setitem__(
            "ERROR_API_READ_KEY", values["ERROR_API_TRIAGE_KEY"]
        ),
    ],
)
def test_preflight_rejects_invalid_security_configuration_without_secrets(
    tmp_path: Path, mutate: Any
) -> None:
    values = _environment()
    marker = "never-print-this-secret"
    values["DB_ERROR_API_PASSWORD"] = marker
    values["ERROR_API_DATABASE_URL"] = (
        f"postgresql+psycopg://db_error_api:{marker}@postgres:5432/reels"
    )
    mutate(values)

    result = _run_preflight(tmp_path, values)

    assert result.returncode != 0
    assert "Preflight FAILED" in result.stdout
    assert marker not in result.stdout + result.stderr


def test_deployment_workflows_start_both_runtimes_and_prod_runs_both_gates() -> None:
    dev = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/deploy-dev.yml").read_text()
    )
    prod = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/deploy-prod.yml").read_text()
    )

    dev_steps = dev["jobs"]["deploy"]["steps"]
    prod_steps = prod["jobs"]["deploy"]["steps"]
    dev_start = next(
        step
        for step in dev_steps
        if step["name"] == "Start migration gates and runtimes"
    )
    prod_start = next(step for step in prod_steps if step["name"] == "Start runtimes")
    assert "downloader error-api" in dev_start["run"]
    assert "downloader error-api" in prod_start["run"]
    assert any("run --rm migrate" in step.get("run", "") for step in prod_steps)
    assert any(
        "run --rm error-api-migrate" in step.get("run", "") for step in prod_steps
    )
