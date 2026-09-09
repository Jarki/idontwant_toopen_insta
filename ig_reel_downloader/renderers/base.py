from __future__ import annotations

from typing import Protocol

from ig_reel_downloader.repository.models import MediaItem

from .models import RenderConstraints, RenderedItem

MIN_TRUNCATED_DESCRIPTION_LENGTH = 20


class MediaRenderer(Protocol):
    def render(
        self, media: MediaItem, constraints: RenderConstraints
    ) -> RenderedItem: ...


class BaseMediaRenderer:
    """Pure, transport-independent default media presentation."""

    def render(self, media: MediaItem, constraints: RenderConstraints) -> RenderedItem:
        attachments = tuple(sorted(media.assets, key=lambda asset: asset.asset_index))
        maximum = (
            constraints.attachment_caption_max_length
            if attachments
            else constraints.standalone_text_max_length
        )
        text = _compose_text(
            self.title(media),
            media.description,
            self.segments(
                media, has_video=any(a.asset_type == "video" for a in attachments)
            ),
            maximum,
        )
        return RenderedItem(source=media, text=text, attachments=attachments)

    def title(self, media: MediaItem) -> str:
        return media.title or media.original_url

    def segments(self, media: MediaItem, *, has_video: bool) -> list[str]:
        del has_video
        return _metrics(media, (("like_count", "❤️"),))


def optional_count(media: MediaItem, key: str) -> int | None:
    value = media.metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or int(value) != value:
        return None
    return int(value)


def _metrics(media: MediaItem, definitions: tuple[tuple[str, str], ...]) -> list[str]:
    return [
        f"{symbol} {value:,}"
        for key, symbol in definitions
        if (value := optional_count(media, key)) is not None
    ]


def _compose_text(
    title: str,
    description: str | None,
    segments: list[str],
    max_length: int,
) -> str:
    if max_length < 1:
        raise ValueError("render text limit must be positive")
    separator = "\n\n"
    kept_segments: list[str] = []
    for segment in segments:
        candidate = " • ".join([*kept_segments, segment])
        # Always reserve at least one character for the higher-priority title.
        if len(separator) + len(candidate) + 1 > max_length:
            break
        kept_segments.append(segment)
    segment_text = " • ".join(kept_segments)
    segment_block_length = len(separator) + len(segment_text) if segment_text else 0

    room_for_title = max_length - segment_block_length
    if len(title) > room_for_title:
        title = (
            (title[: room_for_title - 1] + "…")
            if room_for_title > 1
            else "…"[:room_for_title]
        )
    text = title

    if description:
        room = max_length - len(text) - segment_block_length - len(separator)
        if room >= len(description):
            text += separator + description
        elif room >= MIN_TRUNCATED_DESCRIPTION_LENGTH:
            text += separator + description[: room - 1] + "…"

    if segment_text:
        text += separator + segment_text
    return text
