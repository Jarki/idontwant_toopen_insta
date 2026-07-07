import datetime
from typing import Any, Literal

import pydantic


class IgReel(pydantic.BaseModel):
    id: str
    title: str
    description: str | None
    filepath: str
    url: str
    comments: str
    like_count: int
    created_at: datetime.datetime = pydantic.Field(
        default_factory=datetime.datetime.now
    )


AssetType = Literal["video", "image"]


class MediaAsset(pydantic.BaseModel):
    asset_index: int
    asset_type: AssetType
    filepath: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None


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
