from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, Update

from ig_reel_downloader.downloaders.base import DownloadFailureReason
from ig_reel_downloader.repository.models import MediaItem

logger = logging.getLogger(__name__)
TELEGRAM_MEDIA_GROUP_MAX_ITEMS = 10


@dataclass(frozen=True)
class MediaRenderResult:
    media: MediaItem
    sent: bool
    failure_reason: DownloadFailureReason | None = None


class TelegramMediaRenderer:
    def __init__(
        self,
        telegram_media_write_timeout: float,
        telegram_read_timeout: float,
    ) -> None:
        self.telegram_media_write_timeout = telegram_media_write_timeout
        self.telegram_read_timeout = telegram_read_timeout

    async def render(
        self,
        update: Update,
        media_items: list[MediaItem],
    ) -> list[MediaRenderResult]:
        text_items = [item for item in media_items if _is_text_item(item)]
        supported_media = [item for item in media_items if _is_supported_media(item)]
        unsupported = [
            item
            for item in media_items
            if not _is_text_item(item) and not _is_supported_media(item)
        ]
        results = [
            MediaRenderResult(media=item, sent=False, failure_reason="unsupported")
            for item in unsupported
        ]
        renderable = text_items + supported_media
        if not renderable:
            return results

        chat = update.effective_chat
        if chat is None:
            logger.warning("Cannot send media: update has no effective chat")
            return results + [
                MediaRenderResult(media=item, sent=False, failure_reason="unknown")
                for item in renderable
            ]

        for item in text_items:
            await chat.send_message(
                _format_text_message(item),
                write_timeout=self.telegram_media_write_timeout,
                read_timeout=self.telegram_read_timeout,
            )

        if len(supported_media) == 1 and len(supported_media[0].assets) == 1:
            item = supported_media[0]
            asset = item.assets[0]
            if asset.asset_type == "video":
                await chat.send_video(
                    asset.filepath,
                    caption=_format_caption(item),
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
            else:
                await chat.send_photo(
                    asset.filepath,
                    caption=_format_caption(item),
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
        elif supported_media:
            with ExitStack() as stack:
                # Build flat list of all assets across all supported items,
                # ordered by media item index then asset_index.
                medias: list[InputMediaVideo | InputMediaPhoto] = []
                for item in supported_media:
                    for asset in sorted(item.assets, key=lambda a: a.asset_index):
                        fp = stack.enter_context(Path(asset.filepath).open("rb"))
                        caption = _format_caption(item) if not medias else None
                        if asset.asset_type == "video":
                            media: InputMediaVideo | InputMediaPhoto = InputMediaVideo(
                                fp, caption=caption
                            )
                        else:
                            media = InputMediaPhoto(fp, caption=caption)
                        medias.append(media)
                for media_group in _media_groups(medias):
                    await chat.send_media_group(
                        media_group,
                        write_timeout=self.telegram_media_write_timeout,
                        read_timeout=self.telegram_read_timeout,
                    )
        return results + [
            MediaRenderResult(media=item, sent=True) for item in renderable
        ]


def _media_groups(
    medias: list[InputMediaVideo | InputMediaPhoto],
) -> list[list[InputMediaVideo | InputMediaPhoto]]:
    groups = []
    start = 0
    while start < len(medias):
        remaining = len(medias) - start
        group_size = min(TELEGRAM_MEDIA_GROUP_MAX_ITEMS, remaining)
        if remaining == TELEGRAM_MEDIA_GROUP_MAX_ITEMS + 1:
            group_size -= 1
        groups.append(medias[start : start + group_size])
        start += group_size
    return groups


def _is_text_item(media: MediaItem) -> bool:
    return not media.assets and media.metadata.get("text_only") is True


def _is_supported_media(media: MediaItem) -> bool:
    return bool(media.assets) and all(
        asset.asset_type in {"video", "image"} for asset in media.assets
    )


def _format_caption(media: MediaItem) -> str:
    return _format_item_text(media, max_length=1024)


def _format_text_message(media: MediaItem) -> str:
    max_length = 4096
    if media.metadata.get("body_only") is True:
        text = media.description or media.title
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 1]}…"
    return _format_item_text(media, max_length=max_length)


def _format_item_text(media: MediaItem, *, max_length: int) -> str:
    like_count = int(media.metadata.get("like_count") or 0)
    likes = f" • ❤️ {like_count}"

    title = media.title
    if len(title) + len(likes) > max_length:
        max_title = max_length - len(likes) - 1
        title = (title[:max_title] + "…") if max_title > 0 else "…"

    caption = f"{title}{likes}"

    if media.description:
        desc_with_prefix = f"\n\n{media.description}"
        if len(caption) + len(desc_with_prefix) <= max_length:
            caption += desc_with_prefix
        else:
            room = max_length - len(caption) - 2
            if room >= 1:
                caption += f"\n\n{media.description[: room - 1]}…"

    return caption
