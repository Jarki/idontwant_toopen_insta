import asyncio
import logging
from contextlib import ExitStack
from pathlib import Path

from telegram import InputMediaVideo, Update
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

ReelFetchResult = tuple[models.IgReel | None, str]


class IgReelDownloaderApp:
    def __init__(self, bot_token: str, repository: repository.Repository) -> None:
        self.app: Application = ApplicationBuilder().token(bot_token).build()
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
                await chat.send_media_group(medias)

    def _try_get_reel(self, reel_url: str) -> ReelFetchResult:
        reel_id = utils.get_id_from_url(reel_url)
        if reel_id is None:
            return None, reel_url

        db_reel = self.repository.get_reel_by_id(reel_id)
        if db_reel is not None:
            logger.debug("Find reel %s in database", reel_id)
            if Path(db_reel.filepath).exists():
                return db_reel, reel_url

        reel = utils.download_video(reel_url, self.output_dir, self.cookie_filepath)
        if reel is None:
            return None, reel_url

        self.repository.insert_reel(reel)
        logger.debug("Insert reel %s into database", reel_id)
        return reel, reel_url

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
        errors = [
            f"Could not download {reel_url}" for reel, reel_url in reels if reel is None
        ]
        videos = [reel for reel, _reel_url in reels if reel is not None]

        logger.info("Download %s videos for user %s", len(videos), sender_id)

        if len(videos) > 1:
            await self._send_videos(update, context, videos)
        elif len(videos) == 1:
            await self._send_single_video(update, context, videos[0])

        if errors:
            errors_text = "\n".join(errors)
            chat = update.effective_chat
            if chat is not None:
                await chat.send_message(errors_text)

    def run(self) -> None:
        self.app.run_polling()
