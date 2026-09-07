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

    def insert_media_for_request(
        self,
        media_request_id: int,
        media: models.MediaItem,
    ) -> None:
        """Persist media and link its request in one transaction."""
        raise NotImplementedError

    def upsert_telegram_user(self, user: models.TelegramUser) -> None:
        """Create a Telegram user or refresh their mutable profile fields."""
        raise NotImplementedError

    def insert_media_requests(
        self,
        requests: list[models.MediaRequest],
    ) -> list[int]:
        """Append detected link requests and return their generated IDs."""
        raise NotImplementedError

    def mark_media_request_succeeded(
        self,
        media_request_id: int,
        media_item_id: str,
    ) -> None:
        """Link a request to the media item that satisfied it."""
        raise NotImplementedError

    def mark_media_request_failed(
        self,
        media_request_id: int,
        failure_reason: models.DownloadFailureReason,
        failure_url: str,
        provider_item_id: str | None,
    ) -> None:
        """Record the failure outcome for a request."""
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
