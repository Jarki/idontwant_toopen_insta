from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Chat, InputMediaPhoto, InputMediaVideo, Message, Update
from telegram.constants import MediaGroupLimit, MessageLimit
from telegram.error import BadRequest, TimedOut

from ig_reel_downloader.downloaders.base import DownloadFailureReason
from ig_reel_downloader.renderers import RenderConstraints, RenderedItem
from ig_reel_downloader.repository.models import MediaAsset, MediaItem

logger = logging.getLogger(__name__)
TELEGRAM_RENDER_CONSTRAINTS = RenderConstraints(
    standalone_text_max_length=MessageLimit.MAX_TEXT_LENGTH,
    attachment_caption_max_length=MessageLimit.CAPTION_LENGTH,
)


@dataclass(frozen=True)
class MediaRenderResult:
    media: MediaItem
    sent: bool
    failure_reason: DownloadFailureReason | None = None
    telegram_file_ids: dict[int, str] = field(default_factory=dict)


class MediaRenderTimedOut(TimedOut):
    def __init__(
        self,
        completed_results: list[MediaRenderResult],
        partial_results: list[MediaRenderResult] | None = None,
    ) -> None:
        super().__init__("Timed out while sending media")
        self.completed_results = completed_results
        self.partial_results = partial_results or []


class _MediaItemSendTimedOut(TimedOut):
    def __init__(self, partial_result: MediaRenderResult) -> None:
        super().__init__("Timed out while sending a media item")
        self.partial_result = partial_result


class TelegramMediaSender:
    """Apply Telegram delivery constraints to pure provider-rendered output."""

    def __init__(
        self,
        telegram_media_write_timeout: float,
        telegram_read_timeout: float,
    ) -> None:
        self.telegram_media_write_timeout = telegram_media_write_timeout
        self.telegram_read_timeout = telegram_read_timeout

    async def send(
        self,
        update: Update,
        rendered_items: list[RenderedItem],
    ) -> list[MediaRenderResult]:
        for rendered in rendered_items:
            _validate_rendered_item(rendered)

        if not rendered_items:
            return []
        chat = update.effective_chat
        if chat is None:
            logger.warning("Cannot send media: update has no effective chat")
            return [
                MediaRenderResult(
                    media=rendered.source, sent=False, failure_reason="unknown"
                )
                for rendered in rendered_items
            ]

        completed: list[MediaRenderResult] = []
        try:
            for rendered in rendered_items:
                completed.append(await self._send_item(chat, rendered))
        except _MediaItemSendTimedOut as exc:
            raise MediaRenderTimedOut(completed, [exc.partial_result]) from exc
        except TimedOut as exc:
            raise MediaRenderTimedOut(completed) from exc
        return completed

    async def _send_item(
        self,
        chat: Chat,
        rendered: RenderedItem,
    ) -> MediaRenderResult:
        if not rendered.attachments:
            await chat.send_message(
                rendered.text,
                write_timeout=self.telegram_media_write_timeout,
                read_timeout=self.telegram_read_timeout,
            )
            return MediaRenderResult(media=rendered.source, sent=True)

        sent_file_ids: dict[int, str] = {}
        if len(rendered.attachments) == 1:
            asset = rendered.attachments[0]
            source = asset.telegram_file_id or asset.filepath
            try:
                message = await self._send_single(
                    chat, asset.asset_type, source, rendered.text
                )
            except BadRequest:
                if asset.telegram_file_id is None:
                    raise
                logger.warning(
                    "Stored Telegram file_id failed for %s asset %d; "
                    "uploading the local file instead",
                    rendered.source.id,
                    asset.asset_index,
                )
                message = await self._send_single(
                    chat, asset.asset_type, asset.filepath, rendered.text
                )
            if (file_id := _message_file_id(message, asset.asset_type)) is not None:
                sent_file_ids[asset.asset_index] = file_id
            return MediaRenderResult(
                media=rendered.source,
                sent=True,
                telegram_file_ids=sent_file_ids,
            )

        descriptors = [
            (asset, rendered.text if index == 0 else None)
            for index, asset in enumerate(rendered.attachments)
        ]
        try:
            for descriptor_group in _media_groups(descriptors):
                with ExitStack() as stack:
                    medias = [
                        _input_media(asset, caption, stack)
                        for asset, caption in descriptor_group
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
                            for asset, _ in descriptor_group
                        ):
                            raise
                        logger.warning(
                            "Stored Telegram file_id failed in media group; "
                            "uploading the group again"
                        )
                        with ExitStack() as retry_stack:
                            retry_medias = [
                                _input_media(
                                    asset, caption, retry_stack, force_upload=True
                                )
                                for asset, caption in descriptor_group
                            ]
                            messages = await chat.send_media_group(
                                retry_medias,
                                write_timeout=self.telegram_media_write_timeout,
                                read_timeout=self.telegram_read_timeout,
                            )
                for (asset, _), message in zip(
                    descriptor_group, messages, strict=False
                ):
                    file_id = _message_file_id(message, asset.asset_type)
                    if file_id is not None:
                        sent_file_ids[asset.asset_index] = file_id
        except TimedOut as exc:
            raise _MediaItemSendTimedOut(
                MediaRenderResult(
                    media=rendered.source,
                    sent=False,
                    telegram_file_ids=sent_file_ids,
                )
            ) from exc
        return MediaRenderResult(
            media=rendered.source,
            sent=True,
            telegram_file_ids=sent_file_ids,
        )

    async def _send_single(
        self,
        chat: Chat,
        asset_type: str,
        source: str,
        caption: str,
    ) -> Message:
        if asset_type == "video":
            return await chat.send_video(
                source,
                caption=caption,
                write_timeout=self.telegram_media_write_timeout,
                read_timeout=self.telegram_read_timeout,
            )
        return await chat.send_photo(
            source,
            caption=caption,
            write_timeout=self.telegram_media_write_timeout,
            read_timeout=self.telegram_read_timeout,
        )


def _validate_rendered_item(rendered: RenderedItem) -> None:
    maximum = (
        MessageLimit.CAPTION_LENGTH
        if rendered.attachments
        else MessageLimit.MAX_TEXT_LENGTH
    )
    if not rendered.text and not rendered.attachments:
        raise ValueError("renderer returned neither text nor attachments")
    if len(rendered.text) > maximum:
        raise ValueError(
            f"renderer returned {len(rendered.text)} characters; maximum is {maximum}"
        )

    source_assets = {asset.asset_index: asset for asset in rendered.source.assets}
    if len(source_assets) != len(rendered.source.assets):
        raise ValueError("source media has duplicate attachment indexes")
    if rendered.source.assets and not rendered.attachments:
        raise ValueError("renderer dropped all source attachments")

    rendered_indexes: set[int] = set()
    for asset in rendered.attachments:
        if asset.asset_type not in {"video", "image"}:
            raise ValueError("renderer returned an unsupported attachment type")
        if asset.asset_index in rendered_indexes:
            raise ValueError("renderer returned duplicate attachment indexes")
        rendered_indexes.add(asset.asset_index)
        if source_assets.get(asset.asset_index) != asset:
            raise ValueError("renderer returned an attachment outside its source item")


def _input_media(
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
    groups: list[list[MediaGroupItem]] = []
    start = 0
    while start < len(medias):
        remaining = len(medias) - start
        group_size = min(MediaGroupLimit.MAX_MEDIA_LENGTH, remaining)
        if remaining == MediaGroupLimit.MAX_MEDIA_LENGTH + 1:
            group_size -= 1
        groups.append(medias[start : start + group_size])
        start += group_size
    return groups
