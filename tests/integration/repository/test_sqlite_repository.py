import datetime
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from ig_reel_downloader import constants
from ig_reel_downloader.repository import models
from ig_reel_downloader.repository.sqlite import SqliteRepository


def make_reel(
    reel_id: str = "reel-1",
    *,
    title: str = "Original title",
    created_at: datetime.datetime | None = None,
) -> models.IgReel:
    return models.IgReel(
        id=reel_id,
        title=title,
        description="A description",
        filepath=f"output/{reel_id}.mp4",
        url=f"https://www.instagram.com/reel/{reel_id}",
        comments="[]",
        like_count=42,
        created_at=created_at or datetime.datetime.now(),
    )


def raw_reel_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM reels").fetchone()
    assert row is not None
    return int(row[0])


def alembic_config() -> Config:
    return Config("alembic.ini")


def current_alembic_version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def create_legacy_reels_table(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
CREATE TABLE IF NOT EXISTS reels (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    filepath TEXT,
    url TEXT,
    like_count INTEGER,
    created_at DATETIME,
    comments TEXT
);
            """
        )
        conn.commit()


def insert_legacy_reel(db_path: Path, reel: models.IgReel) -> None:
    create_legacy_reels_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
INSERT OR REPLACE INTO reels (id, title, description, filepath, url, like_count, created_at, comments)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                reel.id,
                reel.title,
                reel.description,
                reel.filepath,
                reel.url,
                reel.like_count,
                reel.created_at.isoformat(sep=" "),
                reel.comments,
            ),
        )
        conn.commit()


def test_create_database_creates_reels_table(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'reels'"
        ).fetchone()
    assert row == ("reels",)
    assert current_alembic_version(db_path) == "20260707_0001"


def test_insert_and_get_reel_round_trips_all_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    reel = make_reel()

    repository.insert_reel(reel)

    assert repository.get_reel_by_id(reel.id) == reel


def test_insert_reel_replaces_same_id_and_preserves_other_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    repository.insert_reel(make_reel("reel-1", title="Old title"))
    repository.insert_reel(make_reel("reel-2", title="Other title"))

    repository.insert_reel(make_reel("reel-1", title="New title"))

    updated = repository.get_reel_by_id("reel-1")
    other = repository.get_reel_by_id("reel-2")
    assert updated is not None
    assert updated.title == "New title"
    assert other is not None
    assert other.title == "Other title"
    assert raw_reel_count(db_path) == 2


def test_get_reel_by_id_returns_none_for_stale_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    stale_reel = make_reel(
        created_at=datetime.datetime.now()
        - constants.REEL_STALE_TIME
        - datetime.timedelta(minutes=1)
    )
    repository.insert_reel(stale_reel)

    assert repository.get_reel_by_id(stale_reel.id) is None


def test_get_reel_by_id_returns_fresh_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    fresh_reel = make_reel(
        created_at=datetime.datetime.now()
        - constants.REEL_STALE_TIME
        + datetime.timedelta(minutes=1)
    )
    repository.insert_reel(fresh_reel)

    assert repository.get_reel_by_id(fresh_reel.id) == fresh_reel


def test_repository_reads_legacy_sqlite_rows_without_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    legacy_reel = make_reel("legacy-reel", title="Legacy title")
    insert_legacy_reel(db_path, legacy_reel)
    repository = SqliteRepository(str(db_path))

    assert repository.get_reel_by_id(legacy_reel.id) == legacy_reel


def test_create_database_is_idempotent_for_new_database(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))

    repository.create_database()
    repository.create_database()

    assert current_alembic_version(db_path) == "20260707_0001"


def test_create_database_uses_repository_path_over_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_db_path = tmp_path / "env" / "reels.db"
    repo_db_path = tmp_path / "repo" / "reels.db"
    monkeypatch.setenv("DB_PATH", str(env_db_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{env_db_path}")
    repo_db_path.parent.mkdir()
    repository = SqliteRepository(str(repo_db_path))

    repository.create_database()

    assert current_alembic_version(repo_db_path) == "20260707_0001"
    assert not env_db_path.exists()


def test_create_database_rejects_existing_reels_table_missing_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE reels (id TEXT PRIMARY KEY)")
        conn.commit()
    repository = SqliteRepository(str(db_path))

    with pytest.raises(RuntimeError, match="missing required columns"):
        repository.create_database()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is None


def test_create_database_rejects_existing_reels_table_without_id_constraint(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
CREATE TABLE reels (
    id TEXT,
    title TEXT,
    description TEXT,
    filepath TEXT,
    url TEXT,
    like_count INTEGER,
    created_at DATETIME,
    comments TEXT
);
            """
        )
        conn.commit()
    repository = SqliteRepository(str(db_path))

    with pytest.raises(RuntimeError, match="id as a primary key or unique column"):
        repository.create_database()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is None


def test_alembic_downgrade_preserves_reels_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    repository.insert_reel(make_reel("downgrade-reel"))
    config = alembic_config()
    config.attributes["database_url"] = f"sqlite:///{db_path}"

    command.downgrade(config, "base")

    assert raw_reel_count(db_path) == 1


def test_alembic_cli_db_path_creates_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "missing-parent" / "reels.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    config = alembic_config()

    command.upgrade(config, "head")

    assert current_alembic_version(db_path) == "20260707_0001"


def test_create_database_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    existing_reel = make_reel("existing-reel", title="Existing title")
    insert_legacy_reel(db_path, existing_reel)
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    assert repository.get_reel_by_id(existing_reel.id) == existing_reel
    assert raw_reel_count(db_path) == 1
    assert current_alembic_version(db_path) == "20260707_0001"

    repository.create_database()

    assert repository.get_reel_by_id(existing_reel.id) == existing_reel
    assert raw_reel_count(db_path) == 1
    assert current_alembic_version(db_path) == "20260707_0001"
