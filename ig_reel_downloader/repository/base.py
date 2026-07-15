from typing import Protocol

from . import models


class Repository(Protocol):
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

    def add_judgmental_animation_file_id(
        self,
        file_id: str,
        file_unique_id: str | None,
    ) -> None:
        """Store or refresh a Telegram animation file_id for judgmental replies."""
        raise NotImplementedError

    def list_judgmental_animation_file_ids(self) -> list[str]:
        """Return stored Telegram animation file_ids for judgmental replies."""
        raise NotImplementedError

    def delete_judgmental_animation_file_id(self, file_id: str) -> None:
        """Forget an invalid Telegram animation file_id."""
        raise NotImplementedError
