import asyncio
import logging
from contextlib import suppress

from telegram import Update
from telegram.error import TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from .downloaders import DownloaderRegistry, DownloadFailureReason, ResolvedUrlMatch
from .media_fetch import MediaFetchResult, MediaFetchService
from .repository import models
from .telegram_renderer import TelegramMediaRenderer

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT = 120.0
DEFAULT_TELEGRAM_READ_TIMEOUT = 30.0


class IgReelDownloaderApp:
    def __init__(
        self,
        bot_token: str,
        registry: DownloaderRegistry,
        fetch_service: MediaFetchService,
        renderer: TelegramMediaRenderer,
        telegram_media_write_timeout: float = DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT,
        telegram_read_timeout: float = DEFAULT_TELEGRAM_READ_TIMEOUT,
    ) -> None:
        self.telegram_media_write_timeout = telegram_media_write_timeout
        self.telegram_read_timeout = telegram_read_timeout
        self.registry = registry
        self.fetch_service = fetch_service
        self.renderer = renderer
        self.app: Application = (
            ApplicationBuilder()
            .token(bot_token)
            .media_write_timeout(telegram_media_write_timeout)
            .read_timeout(telegram_read_timeout)
            .build()
        )
        self.app.add_handler(MessageHandler(filters.TEXT, self._message_handler))

    def _format_download_error(
        self,
        reel_url: str,
        failure_reason: DownloadFailureReason | None,
    ) -> str:
        if failure_reason == "auth":
            return f"Could not download (auth expired): {reel_url}"
        return f"Could not download {reel_url}"

    async def _get_media_items(
        self,
        matches: list[ResolvedUrlMatch],
    ) -> list[MediaFetchResult]:
        tasks = [
            asyncio.to_thread(self.fetch_service.fetch, match) for match in matches
        ]
        return list(await asyncio.gather(*tasks))

    async def _message_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        sender = update.effective_sender
        sender_id = sender.id if sender is not None else "unknown"
        logger.debug("Got message from %s", sender_id)

        message = update.message
        if message is None or not message.text:
            return

        matches = self.registry.extract_matches(message.text)
        if not matches:
            return

        fetch_results = await self._get_media_items(matches)
        errors: list[str] = []
        media_items: list[models.MediaItem] = []
        for result in fetch_results:
            if result.media is None:
                errors.append(
                    self._format_download_error(result.url, result.failure_reason)
                )
            else:
                media_items.append(result.media)

        logger.info("Download %s videos for user %s", len(media_items), sender_id)

        try:
            render_results = await self.renderer.render(update, media_items)
        except TimedOut:
            logger.exception(
                "Timed out while sending %s videos for user %s. "
                "Increase TELEGRAM_MEDIA_WRITE_TIMEOUT if uploads are slow.",
                len(media_items),
                sender_id,
            )
            chat = update.effective_chat
            if chat is not None:
                with suppress(TimedOut):
                    await chat.send_message(
                        "Timed out while uploading video(s) to Telegram. "
                        "The file may be large or the network may be slow."
                    )
        else:
            for render_result in render_results:
                if not render_result.sent:
                    errors.append(
                        self._format_download_error(
                            render_result.media.original_url,
                            render_result.failure_reason,
                        )
                    )

        if errors:
            errors_text = "\n".join(errors)
            chat = update.effective_chat
            if chat is not None:
                await chat.send_message(errors_text)

    def run(self) -> None:
        self.app.run_polling()
