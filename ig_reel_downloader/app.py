import asyncio
import logging
import os

from telegram import Update, InputMediaVideo
from telegram.ext import MessageHandler, ApplicationBuilder, filters, ContextTypes

from . import utils
from . import repository
from .repository import models


logger = logging.getLogger(__name__)

class IgReelDownloaderApp:
    def __init__(self, bot_token: str, repository: repository.Repository):
        self.app = ApplicationBuilder().token(bot_token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._message_handler))
        self.repository = repository

    def set_downloader_config(self, output: str, cookie_filepath: str):
        self.output_dir = output
        self.cookie_filepath = cookie_filepath

    async def _send_single_video(self, update: Update, context: ContextTypes, video: models.IgReel) -> None:
        await update.effective_chat.send_video(
            video.filepath,
            caption=video.title + "\n" + video.description,
        )

    async def _send_videos(self, update: Update, context: ContextTypes, videos: list[models.IgReel]) -> None:
        medias = []
        errors = []
        for v in videos:
            if v.filepath is None:
                errors.append(f"Could not download {v.url}")
                continue
            with open(v.filepath, 'rb') as f:
                medias.append(InputMediaVideo(f))
        await update.effective_chat.send_media_group(medias)

    def _try_get_reel(self, reel_url: str) -> tuple[models.IgReel|None, str]: 
        reel_id = utils.get_id_from_url(reel_url)
        db_reel = self.repository.get_reel_by_id(reel_id)
        if db_reel is not None:
            logger.debug(f"Find reel {reel_id} in database")
            if os.path.exists(db_reel.filepath):
                return db_reel, reel_url
        reel = utils.download_video(reel_url, self.output_dir, self.cookie_filepath)
        self.repository.insert_reel(reel)
        logger.debug(f"Insert reel {reel_id} into database")
        return reel, reel_url
    
    async def _get_reels(self, reel_urls: list[str]) -> list[models.IgReel]:
        tasks = [asyncio.to_thread(self._try_get_reel, reel_url) for reel_url in reel_urls]
        reels = await asyncio.gather(*tasks)
        return reels

    async def _message_handler(self, update: Update, context):
        logger.debug(f"Got message from {update.effective_sender.id}")
        text = update.message.text
        if not text:
            return
        
        urls = utils.get_urls_from_text(text)
        if not urls:
            return

        reels = await self._get_reels(urls)
        errors = [f"Could not download {reel.url}" for reel in reels if reel[0] is None]
        videos = [r[0] for r in reels if r[0] is not None]

        logger.info(f"Download {len(videos)} videos for user {update.effective_sender.id}")

        if len(videos) > 1:
            await self._send_videos(update, context, videos)
        elif len(videos) == 1:
            await self._send_single_video(update, context, videos[0])
        if errors:
            errors = '\n'.join(errors)
            await update.effective_chat.send_message(errors)

    def run(self):
        self.app.run_polling()
