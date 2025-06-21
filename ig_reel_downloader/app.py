import asyncio
import logging

from telegram import Update, InputMediaVideo
from telegram.ext import MessageHandler, ApplicationBuilder, filters

from . import utils


logger = logging.getLogger(__name__)

class IgReelDownloaderApp:
    def __init__(self, bot_token: str):
        self.app = ApplicationBuilder().token(bot_token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._message_handler))

    def set_downloader_config(self, output: str, cookie_filepath: str):
        self.output_dir = output
        self.cookie_filepath = cookie_filepath

    async def _message_handler(self, update: Update, context):
        logger.debug(f"Got message from {update.effective_sender.id}")
        text = update.message.text
        if not text:
            return
        
        urls = utils.get_urls_from_text(text)
        if not urls:
            return
        
        async def _download_video(url: str):
            return (await utils.download_video_async(url, self.output_dir, self.cookie_filepath)), url

        tasks = [_download_video(url) for url in urls] 
        files = await asyncio.gather(*tasks)
        medias = []
        errors = []
        for file_and_url in files:
            file_, url = file_and_url
            if file_ is None:
                errors.append(f"Could not download {url}")
                continue
            with open(file_, 'rb') as f:
                medias.append(InputMediaVideo(f))

        errors = '\n'.join(errors)
        logger.info(f"Downloaded {len(medias)} videos for user {update.effective_sender.id}")
        if medias:
            await update.effective_chat.send_media_group(medias)
        if errors:
            await update.effective_chat.send_message(errors)

    def run(self):
        self.app.run_polling()
