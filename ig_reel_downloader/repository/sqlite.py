import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from . import models
from .orm import Base, ReelRow, from_orm, to_orm
from .. import constants


logger = logging.getLogger(__name__)


class SqliteRepository:
    def __init__(self, db_path: str = "data/reels.db"):
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_database(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_reel_by_id(self, reel_id: str) -> models.IgReel | None:
        stale_cutoff = datetime.datetime.now() - constants.REEL_STALE_TIME
        stmt = select(ReelRow).where(
            ReelRow.id == reel_id,
            ReelRow.created_at > stale_cutoff,
        )
        try:
            async with self._session_factory() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
        except Exception:
            logger.exception(f"Failed to get reel by id: {reel_id}")
            return None
        if row is None:
            return None
        return from_orm(row)

    async def insert_reel(self, reel: models.IgReel) -> None:
        row = to_orm(reel)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.merge(row)
            logger.debug(f"Insert reel {reel.id}")
        except Exception:
            logger.exception(f"Failed to insert reel: {reel.id}")
