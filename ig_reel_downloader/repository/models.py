import datetime
from typing import Any, Literal

import pydantic

AssetType = Literal["video", "image"]
DownloadFailureReason = Literal["auth", "blocked", "unsupported", "unknown"]


class TelegramUser(pydantic.BaseModel):
    id: int
    username: str | None
    first_name: str
    last_name: str | None
    language_code: str | None
    is_bot: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MediaRequest(pydantic.BaseModel):
    telegram_user_id: int | None
    url: str
    normalized_url: str | None
    provider: str
    media_kind: str
    provider_item_id: str | None
    created_at: datetime.datetime


class MediaAsset(pydantic.BaseModel):
    asset_index: int
    asset_type: AssetType
    filepath: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    telegram_file_id: str | None = None


class MediaItem(pydantic.BaseModel):
    id: str
    provider: str
    media_kind: str
    provider_item_id: str
    original_url: str
    title: str
    description: str | None
    metadata: dict[str, Any] = pydantic.Field(default_factory=dict)
    assets: list[MediaAsset]
    created_at: datetime.datetime
    updated_at: datetime.datetime
