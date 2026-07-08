import datetime
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ig_reel_downloader import constants
from ig_reel_downloader.repository import models
from ig_reel_downloader.repository.sqlite import SqliteRepository


def make_reel(
    reel_id: str = "reel-1",
    *,
    title: str = "Original title",
    created_at: datetime.datetime | None = None,
) -> models.IgReel:
    kwargs: dict[str, object] = {
        "id": reel_id,
        "title": title,
        "description": "A description",
        "filepath": f"output/{reel_id}.mp4",
        "url": f"https://www.instagram.com/reel/{reel_id}",
        "comments": "[]",
        "like_count": 42,
    }
    if created_at is not None:
        kwargs["created_at"] = created_at
    return models.IgReel(**kwargs)


def raw_reel_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM reels").fetchone()
    assert row is not None
    return int(row[0])


def raw_reel_title(db_path: Path, reel_id: str) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT title FROM reels WHERE id = ?",
            (reel_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row[0])


def make_media_item(
    item_id: str = "ABC123",
    *,
    title: str = "Original title",
    created_at: datetime.datetime | None = None,
    updated_at: datetime.datetime | None = None,
    asset_indexes: list[int] | None = None,
) -> models.MediaItem:
    now = datetime.datetime.now()
    created = created_at or now
    updated = updated_at or created
    indexes = asset_indexes or [0]
    return models.MediaItem(
        id=f"instagram:reel:{item_id}",
        provider="instagram",
        media_kind="reel",
        provider_item_id=item_id,
        original_url=f"https://www.instagram.com/reel/{item_id}",
        title=title,
        description="A description",
        metadata={"like_count": 42, "comments": []},
        assets=[
            models.MediaAsset(
                asset_index=index,
                asset_type="video",
                filepath=f"output/{item_id}-{index}.mp4",
            )
            for index in indexes
        ],
        created_at=created,
        updated_at=updated,
    )


def raw_media_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()
    assert row is not None
    return int(row[0])


def alembic_config() -> Config:
    return Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))


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
    assert current_alembic_version(db_path) == "20260707_0002"


def test_create_database_creates_generic_media_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    with sqlite3.connect(db_path) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"reels", "media_items", "media_assets"}.issubset(table_names)
    assert current_alembic_version(db_path) == "20260707_0002"


def test_insert_and_get_media_round_trips_item_with_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    media = make_media_item(asset_indexes=[1, 0])

    repository.insert_media(media)

    result = repository.get_media_by_provider_item("instagram", "reel", "ABC123")
    assert result is not None
    assert result.id == media.id
    assert result.metadata == {"like_count": 42, "comments": []}
    assert [asset.asset_index for asset in result.assets] == [0, 1]
    assert raw_media_count(db_path) == 1


def test_get_media_by_provider_item_returns_none_for_stale_updated_at(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    stale = make_media_item(
        updated_at=datetime.datetime.now()
        - constants.CACHE_STALE_TIME
        - datetime.timedelta(minutes=1)
    )
    repository.insert_media(stale)

    assert repository.get_media_by_provider_item("instagram", "reel", "ABC123") is None


def test_insert_media_upsert_preserves_created_at_and_refreshes_assets(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    created = datetime.datetime.now() - datetime.timedelta(hours=2)
    first = make_media_item(
        "ABC123",
        title="Old",
        created_at=created,
        updated_at=created,
        asset_indexes=[0, 1],
    )
    refreshed_at = created + datetime.timedelta(hours=1)
    second = make_media_item(
        "ABC123",
        title="New",
        created_at=refreshed_at,
        updated_at=refreshed_at,
        asset_indexes=[0],
    )

    repository.insert_media(first)
    repository.insert_media(second)

    result = repository.get_media_by_provider_item("instagram", "reel", "ABC123")
    assert result is not None
    assert result.title == "New"
    assert result.created_at == created
    assert result.updated_at == refreshed_at
    assert [asset.asset_index for asset in result.assets] == [0]


def test_insert_media_rejects_duplicate_asset_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    media = make_media_item(asset_indexes=[0, 0])

    with pytest.raises(ValueError, match="duplicate asset_index"):
        repository.insert_media(media)


def test_sqlite_foreign_keys_reject_orphan_assets_and_cascade_delete(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))
    repository.create_database()
    repository.insert_media(make_media_item("ABC123"))

    with repository.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO media_assets "
                    "(media_item_id, asset_index, asset_type, filepath, created_at) "
                    "VALUES ('missing', 0, 'video', 'output/missing.mp4', "
                    "CURRENT_TIMESTAMP)"
                )
            )
        connection.execute(
            text("DELETE FROM media_items WHERE id = 'instagram:reel:ABC123'")
        )
        count = connection.execute(
            text("SELECT COUNT(*) FROM media_assets")
        ).scalar_one()

    assert count == 0


def test_generic_migration_copies_legacy_reels_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    legacy = make_reel("legacy-reel", title="Legacy title")
    insert_legacy_reel(db_path, legacy)
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    media = repository.get_media_by_provider_item("instagram", "reel", "legacy-reel")
    assert media is not None
    assert media.id == "instagram:reel:legacy-reel"
    assert media.title == "Legacy title"
    assert media.original_url == legacy.url
    assert media.metadata["like_count"] == 42
    assert media.metadata["comments"] == []
    assert media.assets[0].filepath == legacy.filepath


def test_generic_migration_handles_dirty_legacy_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    create_legacy_reels_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
INSERT INTO reels (id, title, description, filepath, url, like_count, created_at, comments)
VALUES ('dirty', NULL, NULL, '', NULL, NULL, 'not-a-date', 'not-json')
            """
        )
        conn.commit()
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT title, original_url, metadata_json "
            "FROM media_items WHERE provider_item_id = 'dirty'"
        ).fetchone()
        assets = conn.execute(
            "SELECT COUNT(*) FROM media_assets "
            "WHERE media_item_id = 'instagram:reel:dirty'"
        ).fetchone()
    assert row is not None
    assert row[0] == ""
    assert row[1] == "https://www.instagram.com/reel/dirty"
    metadata = json.loads(row[2])
    assert metadata["like_count"] == 0
    assert metadata["comments"] == []
    assert metadata["comments_raw"] == "not-json"
    assert assets == (0,)


def test_generic_migration_rejects_preexisting_generic_table(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE media_items (id TEXT PRIMARY KEY)")
        conn.commit()
    repository = SqliteRepository(str(db_path))

    with pytest.raises(RuntimeError, match="generic media table"):
        repository.create_database()


def test_create_database_is_idempotent_for_new_database(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    repository = SqliteRepository(str(db_path))

    repository.create_database()
    repository.create_database()

    assert current_alembic_version(db_path) == "20260707_0002"


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

    assert current_alembic_version(repo_db_path) == "20260707_0002"
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
    insert_legacy_reel(db_path, make_reel("downgrade-reel"))
    config = alembic_config()
    config.attributes["database_url"] = f"sqlite:///{db_path}"

    command.downgrade(config, "base")

    assert raw_reel_count(db_path) == 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is None


def test_alembic_cli_db_path_creates_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "missing-parent" / "reels.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    config = alembic_config()

    command.upgrade(config, "head")

    assert current_alembic_version(db_path) == "20260707_0002"


def test_create_database_preserves_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    existing_reel = make_reel("existing-reel", title="Existing title")
    insert_legacy_reel(db_path, existing_reel)
    repository = SqliteRepository(str(db_path))

    repository.create_database()

    media = repository.get_media_by_provider_item("instagram", "reel", existing_reel.id)
    assert media is not None
    assert media.title == existing_reel.title
    assert raw_reel_title(db_path, existing_reel.id) == existing_reel.title
    assert raw_reel_count(db_path) == 1
    assert current_alembic_version(db_path) == "20260707_0002"

    repository.create_database()

    media = repository.get_media_by_provider_item("instagram", "reel", existing_reel.id)
    assert media is not None
    assert media.title == existing_reel.title
    assert raw_reel_title(db_path, existing_reel.id) == existing_reel.title
    assert raw_reel_count(db_path) == 1
    assert current_alembic_version(db_path) == "20260707_0002"
