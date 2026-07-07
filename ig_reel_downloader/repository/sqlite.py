import datetime
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

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
    def from_model(cls, reel: models.IgReel) -> "ReelRecord":
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


def _sqlite_url(db_path: str) -> str:
    if db_path == ":memory:":
        return "sqlite:///:memory:"
    return f"sqlite:///{Path(db_path)}"


class SqliteRepository(base.Repository):
    def __init__(self, db_path: str = "data/reels.db") -> None:
        self.db_path = db_path
        self.engine = create_engine(_sqlite_url(db_path))
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_database(self) -> None:
        alembic_config = Config(
            str(Path(__file__).resolve().parents[2] / "alembic.ini")
        )
        with self.engine.begin() as connection:
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")

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
