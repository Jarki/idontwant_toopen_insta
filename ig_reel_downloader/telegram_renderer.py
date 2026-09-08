from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Chat, InputMediaPhoto, InputMediaVideo, Message, Update
from telegram.error import BadRequest, TimedOut

from ig_reel_downloader.downloaders.base import DownloadFailureReason
from ig_reel_downloader.repository.models import MediaAsset, MediaItem

logger = logging.getLogger(__name__)
TELEGRAM_MEDIA_GROUP_MAX_ITEMS = 10


@dataclass(frozen=True)
class MediaRenderResult:
    media: MediaItem
    sent: bool
    failure_reason: DownloadFailureReason | None = None
    telegram_file_ids: dict[int, str] = field(default_factory=dict)


class MediaRenderTimedOut(TimedOut):
    def __init__(self, completed_results: list[MediaRenderResult]) -> None:
        super().__init__("Timed out while rendering media")
        self.completed_results = completed_results


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

        completed_results: list[MediaRenderResult] = []
        sent_file_ids: dict[tuple[int, int], str] = {}
        try:
            for item in text_items:
                await chat.send_message(
                    _format_text_message(item),
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )
                completed_results.append(MediaRenderResult(media=item, sent=True))

            if len(supported_media) == 1 and len(supported_media[0].assets) == 1:
                item = supported_media[0]
                asset = item.assets[0]
                if asset.telegram_file_id is None:
                    message = await self._send_single(
                        chat, item, asset.asset_type, asset.filepath
                    )
                else:
                    try:
                        message = await self._send_single(
                            chat,
                            item,
                            asset.asset_type,
                            asset.telegram_file_id,
                        )
                    except BadRequest:
                        logger.warning(
                            "Stored Telegram file_id failed for %s asset %d; "
                            "uploading the local file instead",
                            item.id,
                            asset.asset_index,
                        )
                        message = await self._send_single(
                            chat, item, asset.asset_type, asset.filepath
                        )
                file_id = _message_file_id(message, asset.asset_type)
                if file_id is not None:
                    sent_file_ids[(id(item), asset.asset_index)] = file_id
                completed_results.append(_sent_result(item, sent_file_ids))
            elif supported_media:
                descriptors: list[tuple[MediaItem, MediaAsset, str | None]] = []
                for item in supported_media:
                    for asset in sorted(item.assets, key=lambda a: a.asset_index):
                        caption = _format_caption(item) if not descriptors else None
                        descriptors.append((item, asset, caption))

                sent_asset_indexes: dict[int, set[int]] = {}
                completed_media_ids: set[int] = set()
                for descriptor_group in _media_groups(descriptors):
                    with ExitStack() as stack:
                        medias = [
                            _input_media(item, asset, caption, stack)
                            for item, asset, caption in descriptor_group
                        ]
                        try:
                            messages = await chat.send_media_group(
                                medias,
                                write_timeout=self.telegram_media_write_timeout,
                                read_timeout=self.telegram_read_timeout,
                            )
                        except BadRequest:
                            if not any(
                                asset.telegram_file_id is not None
                                for _, asset, _ in descriptor_group
                            ):
                                raise
                            logger.warning(
                                "Stored Telegram file_id failed in media group; "
                                "uploading the group again"
                            )
                            with ExitStack() as retry_stack:
                                retry_medias = [
                                    _input_media(
                                        item,
                                        asset,
                                        caption,
                                        retry_stack,
                                        force_upload=True,
                                    )
                                    for item, asset, caption in descriptor_group
                                ]
                                messages = await chat.send_media_group(
                                    retry_medias,
                                    write_timeout=self.telegram_media_write_timeout,
                                    read_timeout=self.telegram_read_timeout,
                                )
                    for item, asset, _ in descriptor_group:
                        sent_asset_indexes.setdefault(id(item), set()).add(
                            asset.asset_index
                        )
                    for (item, asset, _), message in zip(
                        descriptor_group, messages, strict=False
                    ):
                        file_id = _message_file_id(message, asset.asset_type)
                        if file_id is not None:
                            sent_file_ids[(id(item), asset.asset_index)] = file_id
                    for item in supported_media:
                        item_id = id(item)
                        if item_id in completed_media_ids:
                            continue
                        if len(sent_asset_indexes.get(item_id, set())) == len(
                            item.assets
                        ):
                            completed_results.append(_sent_result(item, sent_file_ids))
                            completed_media_ids.add(item_id)
        except TimedOut as exc:
            raise MediaRenderTimedOut(completed_results) from exc

        return results + completed_results

    async def _send_single(
        self,
        chat: Chat,
        item: MediaItem,
        asset_type: str,
        source: str,
    ) -> Message:
        if asset_type == "video":
            return await chat.send_video(
                source,
                caption=_format_caption(item),
                write_timeout=self.telegram_media_write_timeout,
                read_timeout=self.telegram_read_timeout,
            )
        return await chat.send_photo(
            source,
            caption=_format_caption(item),
            write_timeout=self.telegram_media_write_timeout,
            read_timeout=self.telegram_read_timeout,
        )


def _sent_result(
    media: MediaItem,
    sent_file_ids: dict[tuple[int, int], str],
) -> MediaRenderResult:
    return MediaRenderResult(
        media=media,
        sent=True,
        telegram_file_ids={
            asset.asset_index: file_id
            for asset in media.assets
            if (file_id := sent_file_ids.get((id(media), asset.asset_index)))
            is not None
        },
    )


def _input_media(
    item: MediaItem,
    asset: MediaAsset,
    caption: str | None,
    stack: ExitStack,
    *,
    force_upload: bool = False,
) -> InputMediaVideo | InputMediaPhoto:
    source = (
        stack.enter_context(Path(asset.filepath).open("rb"))  # noqa: SIM115
        if force_upload or asset.telegram_file_id is None
        else asset.telegram_file_id
    )
    if asset.asset_type == "video":
        return InputMediaVideo(source, caption=caption)
    return InputMediaPhoto(source, caption=caption)


def _message_file_id(message: Message | None, asset_type: str) -> str | None:
    if message is None:
        return None
    media = (
        message.video
        if asset_type == "video"
        else (message.photo[-1] if message.photo else None)
    )
    return media.file_id if media is not None else None


def _media_groups[MediaGroupItem](
    medias: list[MediaGroupItem],
) -> list[list[MediaGroupItem]]:
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
    return _format_item_text(media, max_length=4096)


def _format_item_text(media: MediaItem, *, max_length: int) -> str:
    like_count = int(media.metadata.get("like_count") or 0)
    likes = f" • ❤️ {like_count}"

    title = _display_title(media)
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


def _display_title(media: MediaItem) -> str:
    if media.provider != "x" or not media.description:
        return media.title

    description_prefix = media.description[:40]
    repeated_text = f" - {description_prefix}"
    if repeated_text in media.title:
        return media.title.partition(repeated_text)[0].rstrip()
    return media.title
