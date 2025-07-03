from typing import Protocol

from . import models 


class Repository(Protocol):
    def create_database(self) -> None:
        """
        Create the database if it doesn't exist
        """

    def get_reel_by_id(self, reel_id: str) -> models.IgReel:
        """
        Retrieve an IgReel by its id
        Reel's created_at should be withing constants.REEL_STALE_TIME hours
        """

    def insert_reel(self, reel: models.IgReel) -> None:
        """
        Insert a reel into the database
        """
