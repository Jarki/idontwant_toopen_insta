from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from error_api.repository.schema import ErrorApiBase
from ig_reel_downloader.repository.schema import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _heads(config_name: str, script_location: str) -> list[str]:
    config = Config(str(PROJECT_ROOT / config_name))
    config.set_main_option("script_location", str(PROJECT_ROOT / script_location))
    return ScriptDirectory.from_config(config).get_heads()


def test_alembic_migrations_have_single_head() -> None:
    heads = _heads("alembic.ini", "migrations")

    assert len(heads) == 1, (
        f"Expected exactly one Alembic migration head, found {len(heads)}: {heads}. "
        "Resolve divergent branches with an Alembic merge migration."
    )


def test_error_api_migrations_have_one_independent_head() -> None:
    main_heads = _heads("alembic.ini", "migrations")
    error_heads = _heads("error_api_alembic.ini", "error_api/migrations")

    assert error_heads == ["20260908_0001"]
    assert set(main_heads).isdisjoint(error_heads)


def test_observability_metadata_is_separate_from_bot_metadata() -> None:
    assert all(
        table.schema == "observability"
        for table in ErrorApiBase.metadata.tables.values()
    )
    assert not any(
        table.schema == "observability" for table in Base.metadata.tables.values()
    )
    assert set(ErrorApiBase.metadata.tables).isdisjoint(Base.metadata.tables)
