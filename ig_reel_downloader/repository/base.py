from typing import Protocol

from . import models


class Repository(Protocol):
    async def create_database(self) -> None:
        """
        Create the database if it doesn't exist
        """

    async def get_reel_by_id(self, reel_id: str) -> models.IgReel | None:
        """
        Retrieve an IgReel by its id
        Reel's created_at should be within constants.REEL_STALE_TIME hours
        """

    async def insert_reel(self, reel: models.IgReel) -> None:
        """
        Insert a reel into the database
        """
