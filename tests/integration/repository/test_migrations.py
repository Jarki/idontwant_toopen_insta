import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

LATEST_REVISION = "20260715_0004"


def alembic_config(db_path: Path | None = None) -> Config:
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    if db_path is not None:
        config.attributes["database_url"] = f"sqlite:///{db_path}"
    return config


def current_alembic_version(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def create_legacy_reels_table(db_path: Path, *, id_constraint: bool = True) -> None:
    id_definition = "TEXT PRIMARY KEY" if id_constraint else "TEXT"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"""
            CREATE TABLE reels (
                id {id_definition},
                title TEXT,
                description TEXT,
                filepath TEXT,
                url TEXT,
                like_count INTEGER,
                created_at DATETIME,
                comments TEXT
            )
            """
        )
        connection.commit()


def insert_legacy_reel(db_path: Path, *, dirty: bool = False) -> None:
    create_legacy_reels_table(db_path)
    values = (
        "dirty" if dirty else "legacy-reel",
        None if dirty else "Legacy title",
        None,
        "" if dirty else "output/legacy-reel.mp4",
        None if dirty else "https://www.instagram.com/reel/legacy-reel",
        None if dirty else 42,
        "not-a-date" if dirty else "2026-07-15 12:00:00",
        "not-json" if dirty else "[]",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO reels (
                id, title, description, filepath, url,
                like_count, created_at, comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        connection.commit()


def test_historical_migrations_create_sqlite_transfer_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"

    command.upgrade(alembic_config(db_path), "head")

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        file_size_type = next(
            row[2]
            for row in connection.execute("PRAGMA table_info(media_assets)")
            if row[1] == "file_size_bytes"
        )
    assert {
        "reels",
        "media_items",
        "media_assets",
        "judgmental_animations",
        "alembic_version",
    } <= tables
    assert file_size_type == "BIGINT"
    assert current_alembic_version(db_path) == LATEST_REVISION


def test_generic_migration_preserves_and_copies_legacy_reel(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    insert_legacy_reel(db_path)
    config = alembic_config(db_path)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        reel = connection.execute(
            "SELECT title FROM reels WHERE id = 'legacy-reel'"
        ).fetchone()
        media = connection.execute(
            """
            SELECT id, title, original_url, metadata_json
            FROM media_items WHERE provider_item_id = 'legacy-reel'
            """
        ).fetchone()
        asset = connection.execute(
            """
            SELECT filepath FROM media_assets
            WHERE media_item_id = 'instagram:reel:legacy-reel'
            """
        ).fetchone()
    assert reel == ("Legacy title",)
    assert media is not None
    assert media[:3] == (
        "instagram:reel:legacy-reel",
        "Legacy title",
        "https://www.instagram.com/reel/legacy-reel",
    )
    assert json.loads(media[3]) == {"like_count": 42, "comments": []}
    assert asset == ("output/legacy-reel.mp4",)
    assert current_alembic_version(db_path) == LATEST_REVISION


def test_generic_migration_normalizes_dirty_legacy_reel(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    insert_legacy_reel(db_path, dirty=True)

    command.upgrade(alembic_config(db_path), "head")

    with sqlite3.connect(db_path) as connection:
        media = connection.execute(
            """
            SELECT title, original_url, metadata_json
            FROM media_items WHERE provider_item_id = 'dirty'
            """
        ).fetchone()
        asset_count = connection.execute(
            """
            SELECT COUNT(*) FROM media_assets
            WHERE media_item_id = 'instagram:reel:dirty'
            """
        ).fetchone()
    assert media is not None
    assert media[:2] == ("", "https://www.instagram.com/reel/dirty")
    assert json.loads(media[2]) == {
        "like_count": 0,
        "comments": [],
        "comments_raw": "not-json",
    }
    assert asset_count == (0,)


def test_generic_migration_rejects_preexisting_generic_table(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE media_items (id TEXT PRIMARY KEY)")
        connection.commit()

    with pytest.raises(RuntimeError, match="generic media table"):
        command.upgrade(alembic_config(db_path), "head")


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ("CREATE TABLE reels (id TEXT PRIMARY KEY)", "missing required columns"),
        (
            """
            CREATE TABLE reels (
                id TEXT, title TEXT, description TEXT, filepath TEXT,
                url TEXT, like_count INTEGER, created_at DATETIME, comments TEXT
            )
            """,
            "id as a primary key or unique column",
        ),
    ],
)
def test_initial_migration_rejects_incompatible_reels_table(
    tmp_path: Path,
    schema: str,
    message: str,
) -> None:
    db_path = tmp_path / "reels.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(schema)
        connection.commit()

    with pytest.raises(RuntimeError, match=message):
        command.upgrade(alembic_config(db_path), "head")

    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is None


def test_alembic_downgrade_preserves_legacy_reels(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    config = alembic_config(db_path)
    command.upgrade(config, "head")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO reels (
                id, title, description, filepath, url,
                like_count, created_at, comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "downgrade-reel",
                "Downgrade",
                None,
                "output/downgrade.mp4",
                "https://www.instagram.com/reel/downgrade-reel",
                0,
                "2026-07-15 12:00:00",
                "[]",
            ),
        )
        connection.commit()

    command.downgrade(config, "base")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM reels").fetchone()
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert count == (1,)
    assert version is None


def test_explicit_sqlite_url_creates_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-parent" / "reels.db"

    command.upgrade(alembic_config(db_path), "head")

    assert current_alembic_version(db_path) == LATEST_REVISION


def test_alembic_requires_explicit_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        command.current(alembic_config())


def test_file_size_widening_preserves_64_bit_sqlite_value(tmp_path: Path) -> None:
    db_path = tmp_path / "reels.db"
    config = alembic_config(db_path)
    command.upgrade(config, "20260710_0003")
    large_file_size = 5_000_000_000
    timestamp = "2026-07-15 12:00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                id, provider, media_kind, provider_item_id, original_url, title,
                description, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "instagram:reel:large",
                "instagram",
                "reel",
                "large",
                "https://www.instagram.com/reel/large",
                "Large fixture",
                None,
                "{}",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_assets (
                media_item_id, asset_index, asset_type, filepath,
                file_size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "instagram:reel:large",
                0,
                "video",
                "output/large.mp4",
                large_file_size,
                timestamp,
            ),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        value = connection.execute(
            "SELECT file_size_bytes FROM media_assets"
        ).fetchone()
        column_type = next(
            row[2]
            for row in connection.execute("PRAGMA table_info(media_assets)")
            if row[1] == "file_size_bytes"
        )
    assert value == (large_file_size,)
    assert column_type == "BIGINT"
