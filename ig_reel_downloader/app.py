import asyncio
import logging
from contextlib import ExitStack, suppress
from pathlib import Path

from telegram import InputMediaVideo, Update
from telegram.error import TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import repository, utils
from .repository import models

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT = 120.0
DEFAULT_TELEGRAM_READ_TIMEOUT = 30.0

ReelFetchResult = tuple[
    models.IgReel | None,
    str,
    utils.DownloadFailureReason | None,
]


class IgReelDownloaderApp:
    def __init__(
        self,
        bot_token: str,
        repository: repository.Repository,
        telegram_media_write_timeout: float = DEFAULT_TELEGRAM_MEDIA_WRITE_TIMEOUT,
        telegram_read_timeout: float = DEFAULT_TELEGRAM_READ_TIMEOUT,
    ) -> None:
        self.telegram_media_write_timeout = telegram_media_write_timeout
        self.telegram_read_timeout = telegram_read_timeout
        self.app: Application = (
            ApplicationBuilder()
            .token(bot_token)
            .media_write_timeout(telegram_media_write_timeout)
            .read_timeout(telegram_read_timeout)
            .build()
        )
        self.app.add_handler(MessageHandler(filters.TEXT, self._message_handler))
        self.repository = repository
        self.output_dir = "output"
        self.cookie_filepath = "cookies.txt"

    def set_downloader_config(self, output: str, cookie_filepath: str) -> None:
        self.output_dir = output
        self.cookie_filepath = cookie_filepath

    async def _form_video_description(self, video: models.IgReel) -> str:
        likes_count = f" • ❤️ {video.like_count}"
        description_str = f"\n\n{video.description}" if video.description else ""
        return f"{video.title}{likes_count}{description_str}"

    async def _send_single_video(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        video: models.IgReel,
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            logger.warning("Cannot send video: update has no effective chat")
            return

        await chat.send_video(
            video.filepath,
            caption=await self._form_video_description(video),
            write_timeout=self.telegram_media_write_timeout,
            read_timeout=self.telegram_read_timeout,
        )

    async def _send_videos(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        videos: list[models.IgReel],
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            logger.warning("Cannot send media group: update has no effective chat")
            return

        with ExitStack() as stack:
            medias = []
            for video in videos:
                video_file = stack.enter_context(Path(video.filepath).open("rb"))
                medias.append(InputMediaVideo(video_file))

            if medias:
                await chat.send_media_group(
                    medias,
                    write_timeout=self.telegram_media_write_timeout,
                    read_timeout=self.telegram_read_timeout,
                )

    def _try_get_reel(self, reel_url: str) -> ReelFetchResult:
        reel_id = utils.get_id_from_url(reel_url)
        if reel_id is None:
            return None, reel_url, "unknown"

        db_reel = self.repository.get_reel_by_id(reel_id)
        if db_reel is not None:
            logger.debug("Find reel %s in database", reel_id)
            if Path(db_reel.filepath).exists():
                return db_reel, reel_url, None

        download_result = utils.download_video_result(
            reel_url,
            self.output_dir,
            self.cookie_filepath,
        )
        if download_result.reel is None:
            return None, reel_url, download_result.failure_reason

        self.repository.insert_reel(download_result.reel)
        logger.debug("Insert reel %s into database", reel_id)
        return download_result.reel, reel_url, None

    def _format_download_error(
        self,
        reel_url: str,
        failure_reason: utils.DownloadFailureReason | None,
    ) -> str:
        if failure_reason == "auth":
            return f"Could not download (auth expired): {reel_url}"
        return f"Could not download {reel_url}"

    async def _get_reels(self, reel_urls: list[str]) -> list[ReelFetchResult]:
        tasks = [
            asyncio.to_thread(self._try_get_reel, reel_url) for reel_url in reel_urls
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

        urls = list(dict.fromkeys(utils.get_urls_from_text(message.text)))
        if not urls:
            return

        reels = await self._get_reels(urls)
        errors: list[str] = []
        videos: list[models.IgReel] = []
        for reel, reel_url, failure_reason in reels:
            if reel is None:
                errors.append(self._format_download_error(reel_url, failure_reason))
            else:
                videos.append(reel)

        logger.info("Download %s videos for user %s", len(videos), sender_id)

        try:
            if len(videos) > 1:
                await self._send_videos(update, context, videos)
            elif len(videos) == 1:
                await self._send_single_video(update, context, videos[0])
        except TimedOut:
            logger.exception(
                "Timed out while sending %s videos for user %s. "
                "Increase TELEGRAM_MEDIA_WRITE_TIMEOUT if uploads are slow.",
                len(videos),
                sender_id,
            )
            chat = update.effective_chat
            if chat is not None:
                with suppress(TimedOut):
                    await chat.send_message(
                        "Timed out while uploading video(s) to Telegram. "
                        "The file may be large or the network may be slow."
                    )

        if errors:
            errors_text = "\n".join(errors)
            chat = update.effective_chat
            if chat is not None:
                await chat.send_message(errors_text)

    def run(self) -> None:
        self.app.run_polling()
