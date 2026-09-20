from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from error_api.app import ApiSettings

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


def _compose_config(
    overlay: str | None = None, **environment_overrides: str
) -> dict[str, Any]:
    command = ["docker", "compose", "-f", str(BASE_COMPOSE)]
    if overlay is not None:
        command.extend(["-f", str(PROJECT_ROOT / overlay)])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env={**os.environ, **_environment(), **environment_overrides},
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
    healthcheck = services["error-api"]["healthcheck"]
    health_command = healthcheck["test"][1]
    assert "/v1/health" in health_command
    assert 'os.environ["ERROR_API_READ_KEY"]' in health_command
    assert _environment()["ERROR_API_READ_KEY"] not in health_command
    assert healthcheck == {
        "test": healthcheck["test"],
        "timeout": "6s",
        "interval": "10s",
        "retries": 6,
        "start_period": "5s",
    }


@pytest.mark.parametrize(
    ("overlay", "tag", "host_port"),
    [
        ("docker/compose.dev.yaml", "latest", "8001"),
        ("docker/compose.prod.yaml", "prod", "8000"),
    ],
)
def test_compose_overlays_use_one_image_and_distinct_api_ports(
    overlay: str, tag: str, host_port: str
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
    assert services["error-api"]["ports"] == [
        {
            "mode": "ingress",
            "target": 8000,
            "published": host_port,
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]


def test_compose_overlay_api_port_can_be_explicitly_overridden() -> None:
    services = _compose_config("docker/compose.dev.yaml", ERROR_API_HOST_PORT="8123")[
        "services"
    ]

    assert services["error-api"]["ports"][0]["published"] == "8123"


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


def _api_settings(values: dict[str, str]) -> ApiSettings:
    def optional(name: str) -> str | None:
        return values.get(name) or None

    return ApiSettings(
        read_key_current=SecretStr(values["ERROR_API_READ_KEY"]),
        read_label_current=values["ERROR_API_READ_LABEL"],
        read_key_next=(
            SecretStr(value) if (value := optional("ERROR_API_READ_KEY_NEXT")) else None
        ),
        read_label_next=optional("ERROR_API_READ_LABEL_NEXT"),
        triage_key_current=SecretStr(values["ERROR_API_TRIAGE_KEY"]),
        triage_label_current=values["ERROR_API_TRIAGE_LABEL"],
        triage_key_next=(
            SecretStr(value)
            if (value := optional("ERROR_API_TRIAGE_KEY_NEXT"))
            else None
        ),
        triage_label_next=optional("ERROR_API_TRIAGE_LABEL_NEXT"),
    )


@pytest.mark.parametrize(
    "rotation",
    [
        {},
        {
            "ERROR_API_READ_KEY_NEXT": "N" * 31 + "=",
            "ERROR_API_READ_LABEL_NEXT": "reader.next-1",
            "ERROR_API_TRIAGE_KEY_NEXT": "Z" * 4095 + "=",
            "ERROR_API_TRIAGE_LABEL_NEXT": "operator_next-1",
        },
    ],
)
def test_every_preflight_accepted_key_configuration_is_runtime_valid(
    tmp_path: Path, rotation: dict[str, str]
) -> None:
    values = {**_environment(), **rotation}
    result = _run_preflight(tmp_path, values)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight PASSED" in result.stdout
    _api_settings(values)


@pytest.mark.parametrize(
    (("name", "value")),
    [
        ("ERROR_API_READ_KEY", "R" * 31 + "!"),
        ("ERROR_API_READ_KEY", "R" * 31 + " "),
        ("ERROR_API_READ_KEY", "R" * 4097),
        ("ERROR_API_READ_KEY", "R" * 31 + "=R"),
        ("ERROR_API_READ_LABEL", ".bad-first"),
        ("ERROR_API_READ_LABEL", "bad label"),
        ("ERROR_API_READ_LABEL", "L" * 129),
        ("ERROR_API_READ_KEY_NEXT", "N" * 31 + "!"),
        ("ERROR_API_READ_LABEL_NEXT", ".bad-next"),
        ("ERROR_API_TRIAGE_KEY_NEXT", "T" * 31 + " "),
        ("ERROR_API_TRIAGE_LABEL_NEXT", "bad next"),
    ],
)
def test_preflight_rejects_runtime_invalid_key_configuration(
    tmp_path: Path, name: str, value: str
) -> None:
    values = _environment()
    values[name] = value
    if name.endswith("_KEY_NEXT"):
        values[name.replace("_KEY_NEXT", "_LABEL_NEXT")] = "rotation"
    elif name.endswith("_LABEL_NEXT"):
        values[name.replace("_LABEL_NEXT", "_KEY_NEXT")] = "N" * 32

    result = _run_preflight(tmp_path, values)

    assert result.returncode != 0
    assert "Preflight FAILED" in result.stdout
    assert value not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("POSTGRES_PASSWORD", "DB_MIGRATION_PASSWORD"),
        ("POSTGRES_PASSWORD", "DB_APP_PASSWORD"),
        ("POSTGRES_PASSWORD", "DB_ERROR_API_PASSWORD"),
        ("DB_MIGRATION_PASSWORD", "DB_APP_PASSWORD"),
        ("DB_MIGRATION_PASSWORD", "DB_ERROR_API_PASSWORD"),
        ("DB_APP_PASSWORD", "DB_ERROR_API_PASSWORD"),
    ],
)
def test_preflight_rejects_database_password_collisions_without_secrets(
    tmp_path: Path, left: str, right: str
) -> None:
    values = _environment()
    values[right] = values[left]

    result = _run_preflight(tmp_path, values)

    assert result.returncode != 0
    assert "security-boundary passwords must be distinct" in result.stdout
    assert values[left] not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DB_ERROR_API_PASSWORD", "change_me_error_api"),
        (
            "ERROR_API_DATABASE_URL",
            "postgresql+psycopg://db_error_api:change_me_error_api@postgres:5432/reels",
        ),
        (
            "ERROR_API_READ_KEY",
            "replace_with_random_read_key_at_least_32_chars",
        ),
        (
            "ERROR_API_TRIAGE_KEY",
            "replace_with_random_triage_key_at_least_32_chars",
        ),
    ],
)
def test_preflight_rejects_documented_error_api_placeholders(
    tmp_path: Path, name: str, value: str
) -> None:
    values = _environment()
    values[name] = value

    result = _run_preflight(tmp_path, values)

    assert result.returncode != 0
    assert "documented example value" in result.stdout
    assert value not in result.stdout + result.stderr


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
    assert "--wait --wait-timeout 90" in dev_start["run"]
    assert "--wait --wait-timeout 90" in prod_start["run"]
