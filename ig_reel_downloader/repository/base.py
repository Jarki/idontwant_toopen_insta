from typing import Protocol

from . import models


class Repository(Protocol):
    def create_database(self) -> None:
        """Apply Alembic migrations for explicit migration/test callers."""
        raise NotImplementedError

    def get_media_by_provider_item(
        self,
        provider: str,
        media_kind: str,
        provider_item_id: str,
    ) -> models.MediaItem | None:
        """Retrieve a fresh media item by provider identity."""
        raise NotImplementedError

    def insert_media(self, media: models.MediaItem) -> None:
        """Insert or refresh a generic media item and replace its assets atomically."""
        raise NotImplementedError
