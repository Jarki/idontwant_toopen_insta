"""Contract tests for PostgreSQLRepository.

These tests require a real PostgreSQL instance.  Set ``DATABASE_URL``
to a ``postgresql+psycopg://`` target (e.g. the local Compose test
database).  Tests are skipped when the env var is absent.
"""

from __future__ import annotations

import datetime
import os
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from ig_reel_downloader import constants
from ig_reel_downloader.repository import models
from ig_reel_downloader.repository.postgres import PostgreSQLRepository
from ig_reel_downloader.repository.schema import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _database_url(name: str) -> str | None:
    url = os.getenv(name)
    if url is None or url.strip() == "":
        return None
    return url


def _make_media_item(
    item_id: str = "instagram:reel:ABC123",
    *,
    provider: str = "instagram",
    media_kind: str = "reel",
    provider_item_id: str = "ABC123",
    title: str = "Test Reel",
    description: str | None = "A test description",
    original_url: str = "https://www.instagram.com/reel/ABC123",
    metadata: dict[str, Any] | None = None,
    asset_indexes: list[int] | None = None,
    created_at: datetime.datetime | None = None,
    updated_at: datetime.datetime | None = None,
) -> models.MediaItem:
    now = datetime.datetime.now()
    if created_at is None:
        created_at = now
    if updated_at is None:
        updated_at = now
    if metadata is None:
        metadata = {"like_count": 42, "comments": []}
    if asset_indexes is None:
        asset_indexes = [0, 1]
    return models.MediaItem(
        id=item_id,
        provider=provider,
        media_kind=media_kind,
        provider_item_id=provider_item_id,
        original_url=original_url,
        title=title,
        description=description,
        metadata=metadata,
        assets=[
            models.MediaAsset(
                asset_index=idx,
                asset_type="video",
                filepath=f"output/video_{idx}.mp4",
                mime_type="video/mp4",
                width=1920,
                height=1080,
                duration_seconds=30.0,
                file_size_bytes=1024 * 1024 * idx if idx > 0 else None,
            )
            for idx in asset_indexes
        ],
        created_at=created_at,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> str:
    url = _database_url("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL not set — skipping PostgreSQL tests")
    return url


@pytest.fixture(scope="module")
def migration_database_url(database_url: str) -> str:
    return _database_url("DB_MIGRATION_URL") or database_url


@pytest.fixture(scope="module")
def engine(migration_database_url: str) -> Any:
    eng = create_engine(migration_database_url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def clean_tables(engine: Any) -> Any:
    """Truncate all repository tables before each contract test."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))
    return engine


@pytest.fixture
def repo(database_url: str, clean_tables: Any) -> PostgreSQLRepository:
    """Return a fresh application-role repository on a migrated schema."""
    return PostgreSQLRepository(database_url)


# ---------------------------------------------------------------------------
# Startup / wiring tests
# ---------------------------------------------------------------------------


def test_startup_missing_database_url_fails() -> None:
    """Repository raises when no URL is provided."""
    with pytest.raises(TypeError):
        PostgreSQLRepository()  # type: ignore[call-arg]


def test_startup_empty_database_url_fails() -> None:
    """Repository raises on empty URL."""
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        PostgreSQLRepository("")


def test_startup_accepts_explicit_postgres_url(database_url: str) -> None:
    """Repository connects eagerly with the configured application role."""
    repository = PostgreSQLRepository(database_url)
    assert repository.database_url == database_url


# ---------------------------------------------------------------------------
# Media round-trip
# ---------------------------------------------------------------------------


def test_insert_and_get_media_round_trips_item_with_assets(
    repo: PostgreSQLRepository,
) -> None:
    media = _make_media_item(asset_indexes=[1, 0])
    repo.insert_media(media)

    result = repo.get_media_by_provider_item("instagram", "reel", "ABC123")
    assert result is not None
    assert result.id == media.id
    assert result.metadata == {"like_count": 42, "comments": []}
    assert [asset.asset_index for asset in result.assets] == [0, 1]


def test_insert_media_persists_all_fields(repo: PostgreSQLRepository) -> None:
    media = _make_media_item(
        title="Exact fields",
        description="A description",
        original_url="https://exact.url/ABC123",
        metadata={"custom": "value", "nested": {"key": 1}},
        asset_indexes=[0],
    )
    repo.insert_media(media)

    result = repo.get_media_by_provider_item("instagram", "reel", "ABC123")
    assert result is not None
    assert result.title == "Exact fields"
    assert result.description == "A description"
    assert result.original_url == "https://exact.url/ABC123"
    assert result.metadata == {"custom": "value", "nested": {"key": 1}}
    assert len(result.assets) == 1
    assert result.assets[0].asset_index == 0
    assert result.assets[0].asset_type == "video"


# ---------------------------------------------------------------------------
# Upsert and created_at preservation
# ---------------------------------------------------------------------------


def test_insert_media_upsert_preserves_created_at_and_refreshes_assets(
    repo: PostgreSQLRepository,
) -> None:
    created = datetime.datetime.now() - datetime.timedelta(hours=2)
    first = _make_media_item(
        "instagram:reel:ABC123",
        title="Old",
        created_at=created,
        updated_at=created,
        asset_indexes=[0, 1],
    )
    refreshed_at = created + datetime.timedelta(hours=1)
    second = _make_media_item(
        "instagram:reel:ABC123",
        title="New",
        created_at=refreshed_at,
        updated_at=refreshed_at,
        asset_indexes=[0],
    )

    repo.insert_media(first)
    repo.insert_media(second)

    result = repo.get_media_by_provider_item("instagram", "reel", "ABC123")
    assert result is not None
    assert result.title == "New"
    assert result.created_at == created
    assert result.updated_at == refreshed_at
    assert [asset.asset_index for asset in result.assets] == [0]


# ---------------------------------------------------------------------------
# Stale cache filtering
# ---------------------------------------------------------------------------


def test_get_media_by_provider_item_returns_none_for_stale_updated_at(
    repo: PostgreSQLRepository,
) -> None:
    stale = _make_media_item(
        updated_at=datetime.datetime.now()
        - constants.CACHE_STALE_TIME
        - datetime.timedelta(minutes=1)
    )
    repo.insert_media(stale)

    assert repo.get_media_by_provider_item("instagram", "reel", "ABC123") is None


# ---------------------------------------------------------------------------
# Duplicate asset indexes
# ---------------------------------------------------------------------------


def test_insert_media_rejects_duplicate_asset_indexes(
    repo: PostgreSQLRepository,
) -> None:
    media = _make_media_item(asset_indexes=[0, 0])
    with pytest.raises(ValueError, match="duplicate asset_index"):
        repo.insert_media(media)


# ---------------------------------------------------------------------------
# Foreign-key cascade
# ---------------------------------------------------------------------------


def test_foreign_keys_reject_orphan_assets_and_cascade_delete(
    repo: PostgreSQLRepository,
) -> None:
    repo.insert_media(_make_media_item("instagram:reel:ABC123"))

    with pytest.raises(IntegrityError), repo.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO media_assets "
                "(media_item_id, asset_index, asset_type, filepath, created_at) "
                "VALUES ('missing', 0, 'video', 'output/missing.mp4', "
                "CURRENT_TIMESTAMP)"
            )
        )

    with repo.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM media_items WHERE id = 'instagram:reel:ABC123'")
        )
        count = connection.execute(
            text("SELECT COUNT(*) FROM media_assets")
        ).scalar_one()

    assert count == 0


# ---------------------------------------------------------------------------
# Invalid metadata
# ---------------------------------------------------------------------------


def test_get_media_by_provider_item_raises_on_invalid_metadata_json(
    repo: PostgreSQLRepository,
) -> None:
    media = _make_media_item()
    repo.insert_media(media)

    with repo.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE media_items SET metadata_json = '[]' "
                "WHERE provider_item_id = 'ABC123'"
            )
        )

    with pytest.raises(
        ValueError, match=r"media_items.metadata_json is not a JSON object"
    ):
        repo.get_media_by_provider_item("instagram", "reel", "ABC123")


# ---------------------------------------------------------------------------
# Judgmental animation file IDs
# ---------------------------------------------------------------------------


def test_judgmental_animation_file_ids_round_trip(
    repo: PostgreSQLRepository,
) -> None:
    repo.add_judgmental_animation_file_id("file-id-1", "unique-id-1")
    repo.add_judgmental_animation_file_id("file-id-2", "unique-id-2")
    repo.add_judgmental_animation_file_id("file-id-1-refreshed", "unique-id-1")

    assert repo.list_judgmental_animation_file_ids() == [
        "file-id-1-refreshed",
        "file-id-2",
    ]

    repo.delete_judgmental_animation_file_id("file-id-2")

    assert repo.list_judgmental_animation_file_ids() == ["file-id-1-refreshed"]


def test_judgmental_animation_update_by_file_id(
    repo: PostgreSQLRepository,
) -> None:
    """Adding the same file_id updates the existing record."""
    repo.add_judgmental_animation_file_id("file-id", "unique-1")
    repo.add_judgmental_animation_file_id("file-id", "unique-2")

    assert repo.list_judgmental_animation_file_ids() == ["file-id"]


def test_judgmental_animation_cross_key_collision(
    repo: PostgreSQLRepository,
) -> None:
    """A new file_id with an existing file_unique_id refreshes old row."""
    repo.add_judgmental_animation_file_id("file-id-old", "unique-collision")
    repo.add_judgmental_animation_file_id("file-id-new", "unique-collision")

    # list order preserves creation order; file_unique_id match wins
    assert repo.list_judgmental_animation_file_ids() == ["file-id-new"]


def test_judgmental_animation_none_file_unique_id(
    repo: PostgreSQLRepository,
) -> None:
    """Multiple rows with ``file_unique_id=None`` are allowed."""
    repo.add_judgmental_animation_file_id("file-id-1", None)
    repo.add_judgmental_animation_file_id("file-id-2", None)

    assert repo.list_judgmental_animation_file_ids() == ["file-id-1", "file-id-2"]


def test_delete_judgmental_animation_unknown(repo: PostgreSQLRepository) -> None:
    """Deleting a non-existent file_id does not raise."""
    repo.delete_judgmental_animation_file_id("does-not-exist")
    assert repo.list_judgmental_animation_file_ids() == []


# ---------------------------------------------------------------------------
# Transaction rollback
# ---------------------------------------------------------------------------


def test_insert_media_rollback_on_error(repo: PostgreSQLRepository) -> None:
    """An error during insert does not leave partial data."""
    media = _make_media_item(asset_indexes=[0, 0])
    with pytest.raises(ValueError, match="duplicate asset_index"):
        repo.insert_media(media)

    assert repo.get_media_by_provider_item("instagram", "reel", "ABC123") is None


def test_judgmental_animation_rollback_on_error(
    repo: PostgreSQLRepository,
) -> None:
    """A cross-key collision rolls back without changing either row."""
    repo.add_judgmental_animation_file_id("file-id-a", "unique-id-a")
    repo.add_judgmental_animation_file_id("file-id-b", "unique-id-b")
    original_list = repo.list_judgmental_animation_file_ids()

    with pytest.raises(IntegrityError):
        repo.add_judgmental_animation_file_id("file-id-b", "unique-id-a")

    assert repo.list_judgmental_animation_file_ids() == original_list
