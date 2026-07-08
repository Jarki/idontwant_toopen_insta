from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from telegram import InputMediaVideo, Update

from ig_reel_downloader.downloaders.base import DownloadFailureReason
from ig_reel_downloader.repository.models import MediaItem

logger = logging.getLogger(__name__)


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
        unsupported = [
            item for item in media_items if not _is_supported_single_video(item)
        ]
        supported = [item for item in media_items if _is_supported_single_video(item)]
        results = [
            MediaRenderResult(media=item, sent=False, failure_reason="unsupported")
            for item in unsupported
        ]
        if not supported:
            return results

        chat = update.effective_chat
        if chat is None:
            logger.warning("Cannot send media: update has no effective chat")
            return results + [
                MediaRenderResult(media=item, sent=False, failure_reason="unknown")
                for item in supported
            ]

        if len(supported) == 1:
            item = supported[0]
            await chat.send_video(
                item.assets[0].filepath,
                caption=_format_caption(item),
                write_timeout=self.telegram_media_write_timeout,
                read_timeout=self.telegram_read_timeout,
            )
        else:
            with ExitStack() as stack:
                medias = [
                    InputMediaVideo(
                        stack.enter_context(Path(item.assets[0].filepath).open("rb"))
                    )
                    for item in supported
                ]
                await chat.send_media_group(
                    medias,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
        return results + [
            MediaRenderResult(media=item, sent=True) for item in supported
        ]


def _is_supported_single_video(media: MediaItem) -> bool:
    return len(media.assets) == 1 and media.assets[0].asset_type == "video"


def _format_caption(media: MediaItem) -> str:
    like_count = int(media.metadata.get("like_count") or 0)
    likes = f" • ❤️ {like_count}"
    description = f"\n\n{media.description}" if media.description else ""
    return f"{media.title}{likes}{description}"
