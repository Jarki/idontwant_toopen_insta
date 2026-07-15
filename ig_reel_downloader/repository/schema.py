"""SQLAlchemy schema records shared by repository and migration metadata.

All ORM model classes live here as the single source of truth. Neither Alembic
environment code nor repository code should define a second ``Base`` or record
set.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Neutral declarative base for every repository backend."""

    pass


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


class JudgmentalAnimationRecord(Base):
    __tablename__ = "judgmental_animations"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            name="uq_judgmental_animations_file_id",
        ),
        UniqueConstraint(
            "file_unique_id",
            name="uq_judgmental_animations_file_unique_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String, nullable=False)
    file_unique_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)


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
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    media_item: Mapped[MediaItemRecord] = relationship(back_populates="assets")
