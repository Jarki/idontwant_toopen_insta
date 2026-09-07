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
# Telegram users and media requests
# ---------------------------------------------------------------------------


def test_user_upsert_request_insert_and_success_linkage(
    repo: PostgreSQLRepository,
) -> None:
    created_at = datetime.datetime.now() - datetime.timedelta(hours=1)
    first_user = models.TelegramUser(
        id=123456789,
        username="old_username",
        first_name="Alice",
        last_name=None,
        language_code="en",
        is_bot=False,
        created_at=created_at,
        updated_at=created_at,
    )
    first_request = models.MediaRequest(
        telegram_user_id=first_user.id,
        url="https://vm.tiktok.com/short",
        normalized_url=None,
        provider="tiktok",
        media_kind="video",
        provider_item_id=None,
        created_at=created_at,
    )
    repo.upsert_telegram_user(first_user)
    first_request_id = repo.insert_media_requests([first_request])[0]

    updated_at = datetime.datetime.now()
    updated_user = first_user.model_copy(
        update={
            "username": "new_username",
            "last_name": "Example",
            "language_code": "uk",
            "updated_at": updated_at,
        }
    )
    second_request = models.MediaRequest(
        telegram_user_id=updated_user.id,
        url="https://www.instagram.com/reel/ABC123?igsh=tracking",
        normalized_url="https://www.instagram.com/reel/ABC123",
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
        created_at=updated_at,
    )
    repo.upsert_telegram_user(updated_user)
    second_request_id = repo.insert_media_requests([second_request])[0]
    media = _make_media_item(asset_indexes=[0])
    repo.insert_media_for_request(second_request_id, media)

    with repo.engine.connect() as connection:
        user_row = (
            connection.execute(
                text("SELECT * FROM telegram_users WHERE id = 123456789")
            )
            .mappings()
            .one()
        )
        request_rows = (
            connection.execute(
                text(
                    "SELECT id, telegram_user_id, url, normalized_url, provider, "
                    "media_kind, provider_item_id, media_item_id, failure_reason, "
                    "failure_url, created_at, completed_at "
                    "FROM media_requests ORDER BY id"
                )
            )
            .mappings()
            .all()
        )

    assert user_row["username"] == "new_username"
    assert user_row["first_name"] == "Alice"
    assert user_row["last_name"] == "Example"
    assert user_row["language_code"] == "uk"
    assert user_row["is_bot"] is False
    assert user_row["created_at"] == created_at
    assert user_row["updated_at"] == updated_at
    assert len(request_rows) == 2
    assert request_rows[0]["telegram_user_id"] == first_user.id
    assert request_rows[0]["media_item_id"] is None
    assert request_rows[0]["completed_at"] is None
    assert request_rows[0]["url"] == "https://vm.tiktok.com/short"
    assert request_rows[0]["provider_item_id"] is None
    assert request_rows[1]["normalized_url"] == (
        "https://www.instagram.com/reel/ABC123"
    )
    assert request_rows[1]["provider_item_id"] == "ABC123"
    assert request_rows[1]["media_item_id"] == media.id
    assert request_rows[1]["failure_reason"] is None
    assert request_rows[1]["failure_url"] is None
    assert request_rows[1]["completed_at"] is not None
    assert [row["id"] for row in request_rows] == [
        first_request_id,
        second_request_id,
    ]


def test_insert_media_for_request_rolls_back_for_unknown_request(
    repo: PostgreSQLRepository,
) -> None:
    media = _make_media_item(asset_indexes=[0])

    with pytest.raises(ValueError, match="Unknown media request id"):
        repo.insert_media_for_request(999, media)

    assert repo.get_media_by_provider_item("instagram", "reel", "ABC123") is None


def test_insert_media_requests_accepts_userless_request(
    repo: PostgreSQLRepository,
) -> None:
    now = datetime.datetime.now()
    request_id = repo.insert_media_requests(
        [
            models.MediaRequest(
                telegram_user_id=None,
                url="https://www.instagram.com/reel/ABC123",
                normalized_url="https://www.instagram.com/reel/ABC123",
                provider="instagram",
                media_kind="reel",
                provider_item_id="ABC123",
                created_at=now,
            )
        ]
    )[0]

    with repo.engine.connect() as connection:
        telegram_user_id = connection.execute(
            text("SELECT telegram_user_id FROM media_requests WHERE id = :id"),
            {"id": request_id},
        ).scalar_one()

    assert telegram_user_id is None


def test_mark_media_request_succeeded_clears_previous_failure(
    repo: PostgreSQLRepository,
) -> None:
    now = datetime.datetime.now()
    request_id = repo.insert_media_requests(
        [
            models.MediaRequest(
                telegram_user_id=None,
                url="https://www.instagram.com/reel/ABC123",
                normalized_url="https://www.instagram.com/reel/ABC123",
                provider="instagram",
                media_kind="reel",
                provider_item_id="ABC123",
                created_at=now,
            )
        ]
    )[0]
    media = _make_media_item(asset_indexes=[0])
    repo.insert_media(media)
    repo.mark_media_request_failed(
        request_id,
        "unknown",
        media.original_url,
        media.provider_item_id,
    )

    repo.mark_media_request_succeeded(request_id, media.id)

    with repo.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT media_item_id, failure_reason, failure_url, completed_at "
                    "FROM media_requests WHERE id = :id"
                ),
                {"id": request_id},
            )
            .mappings()
            .one()
        )

    assert row["media_item_id"] == media.id
    assert row["failure_reason"] is None
    assert row["failure_url"] is None
    assert row["completed_at"] is not None


def test_media_request_outcome_constraint_rejects_invalid_state(
    repo: PostgreSQLRepository,
) -> None:
    now = datetime.datetime.now()
    request_id = repo.insert_media_requests(
        [
            models.MediaRequest(
                telegram_user_id=None,
                url="https://www.instagram.com/reel/ABC123",
                normalized_url=None,
                provider="instagram",
                media_kind="reel",
                provider_item_id="ABC123",
                created_at=now,
            )
        ]
    )[0]

    with pytest.raises(IntegrityError), repo.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE media_requests SET completed_at = :completed_at WHERE id = :id"
            ),
            {"completed_at": now, "id": request_id},
        )


# ---------------------------------------------------------------------------
# Failed request outcomes
# ---------------------------------------------------------------------------


def test_mark_media_requests_failed_records_outcomes(
    repo: PostgreSQLRepository,
) -> None:
    created_at = datetime.datetime.now()
    request_ids = repo.insert_media_requests(
        [
            models.MediaRequest(
                telegram_user_id=None,
                url=url,
                normalized_url=None,
                provider=provider,
                media_kind=media_kind,
                provider_item_id=None,
                created_at=created_at,
            )
            for url, provider, media_kind in (
                ("https://www.instagram.com/reel/ABC123", "instagram", "reel"),
                ("https://youtu.be/unresolved", "youtube", "video"),
            )
        ]
    )

    repo.mark_media_request_failed(
        request_ids[0],
        "auth",
        "https://www.instagram.com/reel/ABC123",
        "ABC123",
    )
    repo.mark_media_request_failed(
        request_ids[1],
        "unknown",
        "https://youtu.be/unresolved",
        None,
    )

    with repo.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT id, provider_item_id, failure_reason, failure_url, "
                    "completed_at FROM media_requests ORDER BY id"
                )
            )
            .mappings()
            .all()
        )

    assert [row["id"] for row in rows] == request_ids
    assert rows[0]["provider_item_id"] == "ABC123"
    assert rows[0]["failure_reason"] == "auth"
    assert rows[0]["failure_url"] == ("https://www.instagram.com/reel/ABC123")
    assert rows[0]["completed_at"] is not None
    assert rows[1]["provider_item_id"] is None
    assert rows[1]["failure_reason"] == "unknown"
    assert rows[1]["failure_url"] == "https://youtu.be/unresolved"
    assert rows[1]["completed_at"] is not None


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
