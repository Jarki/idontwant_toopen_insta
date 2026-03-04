import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import models


class Base(DeclarativeBase):
    pass


class ReelRow(Base):
    __tablename__ = "reels"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    filepath: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    comments: Mapped[str] = mapped_column(Text, nullable=False)


def to_orm(reel: models.IgReel) -> ReelRow:
    return ReelRow(
        id=reel.id,
        title=reel.title,
        description=reel.description,
        filepath=reel.filepath,
        url=reel.url,
        like_count=reel.like_count,
        created_at=reel.created_at,
        comments=reel.comments,
    )


def from_orm(row: ReelRow) -> models.IgReel:
    return models.IgReel(
        id=row.id,
        title=row.title,
        description=row.description,
        filepath=row.filepath,
        url=row.url,
        like_count=row.like_count,
        created_at=row.created_at,
        comments=row.comments,
    )
