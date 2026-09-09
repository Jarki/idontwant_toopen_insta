from __future__ import annotations

from dataclasses import dataclass

from ig_reel_downloader.repository.models import MediaAsset, MediaItem


@dataclass(frozen=True)
class RenderConstraints:
    standalone_text_max_length: int
    attachment_caption_max_length: int


@dataclass(frozen=True)
class RenderedItem:
    source: MediaItem
    text: str
    attachments: tuple[MediaAsset, ...]
