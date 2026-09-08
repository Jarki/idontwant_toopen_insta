from __future__ import annotations

import datetime
import json
from typing import Any, cast

from sqlalchemy import (
    CursorResult,
    create_engine,
    delete,
    func,
    make_url,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from .. import constants
from . import base, models
from .schema import (
    JudgmentalAnimationRecord,
    MediaAssetRecord,
    MediaItemRecord,
    MediaRequestRecord,
    TelegramUserRecord,
)


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
        with self.session_factory() as session:
            _upsert_media(session, media)
            session.commit()

    def insert_media_for_request(
        self,
        media_request_id: int,
        media: models.MediaItem,
    ) -> None:
        with self.session_factory() as session:
            request = _get_media_request(session, media_request_id)
            _upsert_media(session, media)
            _mark_media_request_succeeded(request, media.id)
            session.commit()

    def update_media_asset_telegram_file_id(
        self,
        media_item_id: str,
        asset_index: int,
        telegram_file_id: str,
    ) -> None:
        with self.session_factory() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(MediaAssetRecord)
                    .where(
                        MediaAssetRecord.media_item_id == media_item_id,
                        MediaAssetRecord.asset_index == asset_index,
                    )
                    .values(telegram_file_id=telegram_file_id)
                ),
            )
            if result.rowcount != 1:
                msg = (
                    "Unknown media asset: "
                    f"media_item_id={media_item_id!r}, asset_index={asset_index}"
                )
                raise ValueError(msg)
            session.commit()

    def upsert_telegram_user(self, user: models.TelegramUser) -> None:
        with self.session_factory() as session:
            statement = pg_insert(TelegramUserRecord).values(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
                is_bot=user.is_bot,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[TelegramUserRecord.id],
                    set_={
                        "username": statement.excluded.username,
                        "first_name": statement.excluded.first_name,
                        "last_name": statement.excluded.last_name,
                        "language_code": statement.excluded.language_code,
                        "is_bot": statement.excluded.is_bot,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )
            session.commit()

    def insert_media_requests(
        self,
        requests: list[models.MediaRequest],
    ) -> list[int]:
        records = [
            MediaRequestRecord(
                telegram_user_id=request.telegram_user_id,
                telegram_chat_id=request.telegram_chat_id,
                url=request.url,
                normalized_url=request.normalized_url,
                provider=request.provider,
                media_kind=request.media_kind,
                provider_item_id=request.provider_item_id,
                created_at=request.created_at,
            )
            for request in requests
        ]
        with self.session_factory() as session:
            session.add_all(records)
            session.commit()
            return [record.id for record in records]

    def get_chat_user_stats(
        self,
        telegram_user_id: int,
        telegram_chat_id: int,
    ) -> models.ChatUserStats:
        with self.session_factory() as session:
            requested, delivered, download_failed = session.execute(
                select(
                    func.count(MediaRequestRecord.id),
                    func.count(MediaRequestRecord.delivered_at),
                    func.count(MediaRequestRecord.id).filter(
                        MediaRequestRecord.failure_reason.is_not(None)
                    ),
                ).where(
                    MediaRequestRecord.telegram_chat_id == telegram_chat_id,
                    MediaRequestRecord.telegram_user_id == telegram_user_id,
                )
            ).one()
            type_count = func.count(MediaRequestRecord.id).label("type_count")
            delivered_by_type = session.execute(
                select(
                    MediaItemRecord.provider,
                    MediaItemRecord.media_kind,
                    type_count,
                )
                .join(
                    MediaRequestRecord,
                    MediaRequestRecord.media_item_id == MediaItemRecord.id,
                )
                .where(
                    MediaRequestRecord.telegram_chat_id == telegram_chat_id,
                    MediaRequestRecord.telegram_user_id == telegram_user_id,
                    MediaRequestRecord.delivered_at.is_not(None),
                )
                .group_by(MediaItemRecord.provider, MediaItemRecord.media_kind)
                .order_by(
                    type_count.desc(),
                    MediaItemRecord.provider,
                    MediaItemRecord.media_kind,
                )
            ).all()
        return models.ChatUserStats(
            requested=requested,
            delivered=delivered,
            delivery_failed=requested - delivered - download_failed,
            download_failed=download_failed,
            delivered_by_type=[
                models.MediaTypeCount(
                    provider=provider,
                    media_kind=media_kind,
                    count=count,
                )
                for provider, media_kind, count in delivered_by_type
            ],
        )

    def get_chat_leaderboard(
        self,
        telegram_chat_id: int,
        limit: int = 10,
    ) -> list[models.ChatLeaderboardEntry]:
        media_count = func.count(MediaRequestRecord.id).label("media_count")
        with self.session_factory() as session:
            rows = session.execute(
                select(
                    TelegramUserRecord.username,
                    TelegramUserRecord.first_name,
                    TelegramUserRecord.last_name,
                    media_count,
                )
                .join(
                    MediaRequestRecord,
                    MediaRequestRecord.telegram_user_id == TelegramUserRecord.id,
                )
                .where(MediaRequestRecord.telegram_chat_id == telegram_chat_id)
                .group_by(
                    TelegramUserRecord.id,
                    TelegramUserRecord.username,
                    TelegramUserRecord.first_name,
                    TelegramUserRecord.last_name,
                )
                .order_by(media_count.desc(), TelegramUserRecord.id)
                .limit(limit)
            ).all()
        return [
            models.ChatLeaderboardEntry(
                username=username,
                first_name=first_name,
                last_name=last_name,
                count=count,
            )
            for username, first_name, last_name, count in rows
        ]

    def mark_media_requests_delivered(self, media_request_ids: list[int]) -> None:
        if not media_request_ids:
            return
        with self.session_factory() as session:
            requests = list(
                session.scalars(
                    select(MediaRequestRecord).where(
                        MediaRequestRecord.id.in_(media_request_ids)
                    )
                )
            )
            if len(requests) != len(media_request_ids) or any(
                request.media_item_id is None or request.failure_reason is not None
                for request in requests
            ):
                msg = "Cannot deliver unknown or unsuccessful media requests"
                raise ValueError(msg)
            delivered_at = datetime.datetime.now()
            for request in requests:
                if request.delivered_at is None:
                    request.delivered_at = delivered_at
            session.commit()

    def mark_media_request_succeeded(
        self,
        media_request_id: int,
        media_item_id: str,
    ) -> None:
        with self.session_factory() as session:
            request = _get_media_request(session, media_request_id)
            _mark_media_request_succeeded(request, media_item_id)
            session.commit()

    def mark_media_request_failed(
        self,
        media_request_id: int,
        failure_reason: models.DownloadFailureReason,
        failure_url: str,
        provider_item_id: str | None,
    ) -> None:
        with self.session_factory() as session:
            request = _get_media_request(session, media_request_id)
            request.media_item_id = None
            request.failure_reason = failure_reason
            request.failure_url = failure_url
            if provider_item_id is not None:
                request.provider_item_id = provider_item_id
            request.completed_at = datetime.datetime.now()
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


def _get_media_request(
    session: Session,
    media_request_id: int,
) -> MediaRequestRecord:
    request = session.get(MediaRequestRecord, media_request_id)
    if request is None:
        msg = f"Unknown media request id: {media_request_id}"
        raise ValueError(msg)
    return request


def _mark_media_request_succeeded(
    request: MediaRequestRecord,
    media_item_id: str,
) -> None:
    request.media_item_id = media_item_id
    request.failure_reason = None
    request.failure_url = None
    request.completed_at = datetime.datetime.now()


def _upsert_media(session: Session, media: models.MediaItem) -> None:
    _validate_unique_asset_indexes(media)
    existing = session.get(MediaItemRecord, media.id)
    created_at = existing.created_at if existing is not None else media.created_at
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
        delete(MediaAssetRecord).where(MediaAssetRecord.media_item_id == media.id)
    )
    for asset in media.assets:
        session.add(_asset_model_to_record(media.id, asset, media.updated_at))


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
        telegram_file_id=record.telegram_file_id,
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
        telegram_file_id=asset.telegram_file_id,
        created_at=created_at,
    )
