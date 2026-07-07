from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, cast

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from .. import constants
from . import base, models

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class ReelRecord(Base):
    __tablename__ = "reels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    filepath: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    like_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    comments: Mapped[str] = mapped_column(String)

    @classmethod
    def from_model(cls, reel: models.IgReel) -> ReelRecord:
        return cls(
            id=reel.id,
            title=reel.title,
            description=reel.description,
            filepath=reel.filepath,
            url=reel.url,
            like_count=reel.like_count,
            created_at=reel.created_at,
            comments=reel.comments,
        )

    def to_model(self) -> models.IgReel:
        return models.IgReel(
            id=self.id,
            title=self.title,
            description=self.description,
            filepath=self.filepath,
            url=self.url,
            like_count=self.like_count,
            created_at=self.created_at,
            comments=self.comments,
        )


class MediaItemRecord(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "media_kind",
            "provider_item_id",
            name="uq_media_items_provider_kind_item",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    media_kind: Mapped[str] = mapped_column(String, nullable=False)
    provider_item_id: Mapped[str] = mapped_column(String, nullable=False)
    original_url: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    assets: Mapped[list[MediaAssetRecord]] = relationship(
        back_populates="media_item",
        cascade="all, delete-orphan",
        order_by="MediaAssetRecord.asset_index",
    )


class MediaAssetRecord(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "media_item_id",
            "asset_index",
            name="uq_media_assets_item_index",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_index: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)
    filepath: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    media_item: Mapped[MediaItemRecord] = relationship(back_populates="assets")


def _sqlite_url(db_path: str) -> str:
    if db_path == ":memory:":
        return "sqlite:///:memory:"
    return f"sqlite:///{Path(db_path)}"


class SqliteRepository(base.Repository):
    def __init__(self, db_path: str = "data/reels.db") -> None:
        self.db_path = db_path
        self.engine = create_engine(_sqlite_url(db_path))

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(
            dbapi_connection: Any,
            connection_record: Any,
        ) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_database(self) -> None:
        alembic_config = Config(
            str(Path(__file__).resolve().parents[2] / "alembic.ini")
        )
        with self.engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")

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
            statement = sqlite_insert(MediaItemRecord).values(
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

    def get_reel_by_id(self, reel_id: str) -> models.IgReel | None:
        stale_threshold = datetime.datetime.now() - constants.REEL_STALE_TIME
        try:
            with self.session_factory() as session:
                reel = session.scalar(
                    select(ReelRecord).where(
                        ReelRecord.id == reel_id,
                        ReelRecord.created_at > stale_threshold,
                    )
                )
                if reel is None:
                    return None
                return reel.to_model()
        except Exception as e:
            logger.exception("Failed to get reel by id: %s", e)
            return None

    def insert_reel(self, reel: models.IgReel) -> None:
        values = {
            "id": reel.id,
            "title": reel.title,
            "description": reel.description,
            "filepath": reel.filepath,
            "url": reel.url,
            "like_count": reel.like_count,
            "created_at": reel.created_at,
            "comments": reel.comments,
        }
        statement = sqlite_insert(ReelRecord).values(**values)
        upsert_statement = statement.on_conflict_do_update(
            index_elements=[ReelRecord.id],
            set_={
                "title": statement.excluded.title,
                "description": statement.excluded.description,
                "filepath": statement.excluded.filepath,
                "url": statement.excluded.url,
                "like_count": statement.excluded.like_count,
                "created_at": statement.excluded.created_at,
                "comments": statement.excluded.comments,
            },
        )
        try:
            with self.session_factory() as session:
                session.execute(upsert_statement)
                session.commit()
                logger.debug("Insert reel %s", reel.id)
        except Exception as e:
            logger.exception("Failed to insert reels: %s", e)


def _validate_unique_asset_indexes(media: models.MediaItem) -> None:
    indexes = [asset.asset_index for asset in media.assets]
    if len(indexes) != len(set(indexes)):
        msg = "MediaItem contains duplicate asset_index values"
        raise ValueError(msg)


def _metadata_json_to_dict(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    return {}


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
