from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _assert_single_head(config_path: Path, script_location: Path) -> None:
    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_location))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, (
        f"Expected exactly one Alembic migration head, found {len(heads)}: {heads}. "
        "Resolve divergent branches with an Alembic merge migration."
    )


def test_main_alembic_migrations_have_single_head() -> None:
    _assert_single_head(
        PROJECT_ROOT / "alembic.ini",
        PROJECT_ROOT / "migrations",
    )


def test_error_api_alembic_migrations_have_single_head() -> None:
    _assert_single_head(
        PROJECT_ROOT / "error_api_alembic.ini",
        PROJECT_ROOT / "error_api/migrations",
    )
