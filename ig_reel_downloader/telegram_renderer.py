from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from telegram import InputMediaPhoto, InputMediaVideo, Update

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
        unsupported = [item for item in media_items if not _is_supported(item)]
        supported = [item for item in media_items if _is_supported(item)]
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

        if len(supported) == 1 and len(supported[0].assets) == 1:
            item = supported[0]
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
        else:
            with ExitStack() as stack:
                # Build flat list of all assets across all supported items,
                # ordered by media item index then asset_index.
                medias: list[InputMediaVideo | InputMediaPhoto] = []
                for item in supported:
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
                await chat.send_media_group(
                    medias,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
        return results + [
            MediaRenderResult(media=item, sent=True) for item in supported
        ]


def _is_supported(media: MediaItem) -> bool:
    return bool(media.assets) and all(
        asset.asset_type in {"video", "image"} for asset in media.assets
    )


def _format_caption(media: MediaItem) -> str:
    like_count = int(media.metadata.get("like_count") or 0)
    likes = f" • ❤️ {like_count}"
    description = f"\n\n{media.description}" if media.description else ""
    return f"{media.title}{likes}{description}"
