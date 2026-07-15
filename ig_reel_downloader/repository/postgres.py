from __future__ import annotations

import datetime
import json
from typing import Any, cast

from sqlalchemy import create_engine, delete, make_url, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from .. import constants
from . import base, models
from .schema import JudgmentalAnimationRecord, MediaAssetRecord, MediaItemRecord


class PostgreSQLRepository(base.Repository):
    """Repository backed by PostgreSQL via synchronous SQLAlchemy sessions.

    Schema creation is deliberately excluded; Alembic owns migrations.
    """

    def __init__(self, database_url: str) -> None:
        if not database_url.startswith("postgresql+psycopg://"):
            msg = "database_url must use the postgresql+psycopg:// dialect"
            raise ValueError(msg)
        self.database_url = database_url
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        expected_user = make_url(database_url).username
        with self.engine.connect() as connection:
            actual_user = connection.exec_driver_sql("SELECT current_user").scalar_one()
        if actual_user != expected_user:
            self.engine.dispose()
            msg = (
                f"DATABASE_URL connected as {actual_user!r}, "
                f"expected application role {expected_user!r}"
            )
            raise RuntimeError(msg)

    def get_media_by_provider_item(
        self,
        provider: str,
        media_kind: str,
        provider_item_id: str,
    ) -> models.MediaItem | None:
        stale_threshold = datetime.datetime.now() - constants.CACHE_STALE_TIME
        with self.session_factory() as session:
            item = session.scalar(
                select(MediaItemRecord).where(
                    MediaItemRecord.provider == provider,
                    MediaItemRecord.media_kind == media_kind,
                    MediaItemRecord.provider_item_id == provider_item_id,
                    MediaItemRecord.updated_at > stale_threshold,
                )
            )
            if item is None:
                return None
            return _media_record_to_model(item)

    def insert_media(self, media: models.MediaItem) -> None:
        _validate_unique_asset_indexes(media)
        with self.session_factory() as session:
            existing = session.scalar(
                select(MediaItemRecord).where(MediaItemRecord.id == media.id)
            )
            created_at = (
                existing.created_at if existing is not None else media.created_at
            )
            statement = pg_insert(MediaItemRecord).values(
                id=media.id,
                provider=media.provider,
                media_kind=media.media_kind,
                provider_item_id=media.provider_item_id,
                original_url=media.original_url,
                title=media.title,
                description=media.description,
                metadata_json=json.dumps(media.metadata),
                created_at=created_at,
                updated_at=media.updated_at,
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[MediaItemRecord.id],
                    set_={
                        "original_url": statement.excluded.original_url,
                        "title": statement.excluded.title,
                        "description": statement.excluded.description,
                        "metadata_json": statement.excluded.metadata_json,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )
            session.execute(
                delete(MediaAssetRecord).where(
                    MediaAssetRecord.media_item_id == media.id
                )
            )
            for asset in media.assets:
                session.add(_asset_model_to_record(media.id, asset, media.updated_at))
            session.commit()

    def add_judgmental_animation_file_id(
        self,
        file_id: str,
        file_unique_id: str | None,
    ) -> None:
        now = datetime.datetime.now()
        with self.session_factory() as session:
            existing: JudgmentalAnimationRecord | None = None
            if file_unique_id is not None:
                existing = session.scalar(
                    select(JudgmentalAnimationRecord).where(
                        JudgmentalAnimationRecord.file_unique_id == file_unique_id
                    )
                )
            if existing is None:
                existing = session.scalar(
                    select(JudgmentalAnimationRecord).where(
                        JudgmentalAnimationRecord.file_id == file_id
                    )
                )
            if existing is None:
                session.add(
                    JudgmentalAnimationRecord(
                        file_id=file_id,
                        file_unique_id=file_unique_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing.file_id = file_id
                existing.file_unique_id = file_unique_id or existing.file_unique_id
                existing.updated_at = now
            session.commit()

    def list_judgmental_animation_file_ids(self) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(JudgmentalAnimationRecord.file_id).order_by(
                        JudgmentalAnimationRecord.id
                    )
                )
            )

    def delete_judgmental_animation_file_id(self, file_id: str) -> None:
        with self.session_factory() as session:
            session.execute(
                delete(JudgmentalAnimationRecord).where(
                    JudgmentalAnimationRecord.file_id == file_id
                )
            )
            session.commit()


# ------------------------------------------------------------------
# Repository/model conversion helpers.
# ------------------------------------------------------------------


def _validate_unique_asset_indexes(media: models.MediaItem) -> None:
    indexes = [asset.asset_index for asset in media.assets]
    if len(indexes) != len(set(indexes)):
        msg = "MediaItem contains duplicate asset_index values"
        raise ValueError(msg)


def _metadata_json_to_dict(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    msg = f"media_items.metadata_json is not a JSON object: {value!r}"
    raise ValueError(msg)


def _media_record_to_model(record: MediaItemRecord) -> models.MediaItem:
    return models.MediaItem(
        id=record.id,
        provider=record.provider,
        media_kind=record.media_kind,
        provider_item_id=record.provider_item_id,
        original_url=record.original_url,
        title=record.title,
        description=record.description,
        metadata=_metadata_json_to_dict(record.metadata_json),
        assets=[_asset_record_to_model(asset) for asset in record.assets],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _asset_record_to_model(record: MediaAssetRecord) -> models.MediaAsset:
    return models.MediaAsset(
        asset_index=record.asset_index,
        asset_type=cast(models.AssetType, record.asset_type),
        filepath=record.filepath,
        mime_type=record.mime_type,
        width=record.width,
        height=record.height,
        duration_seconds=record.duration_seconds,
        file_size_bytes=record.file_size_bytes,
    )


def _asset_model_to_record(
    media_item_id: str,
    asset: models.MediaAsset,
    created_at: datetime.datetime,
) -> MediaAssetRecord:
    return MediaAssetRecord(
        media_item_id=media_item_id,
        asset_index=asset.asset_index,
        asset_type=asset.asset_type,
        filepath=asset.filepath,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        duration_seconds=asset.duration_seconds,
        file_size_bytes=asset.file_size_bytes,
        created_at=created_at,
    )
