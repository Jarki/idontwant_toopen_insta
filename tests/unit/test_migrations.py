from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_alembic_migrations_have_single_head() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert len(heads) == 1, (
        f"Expected exactly one Alembic migration head, found {len(heads)}: {heads}. "
        "Resolve divergent branches with an Alembic merge migration."
    )
