import asyncio
import logging
import os
import re
from typing import Coroutine

import yt_dlp

logger = logging.getLogger(__name__)

def download_video(url: str, output_dir: str, cookie_filepath: str="cookies.txt") -> int:
    ydl_opts = {
        'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
        'format': 'best',
        'quiet': True,
        'cookiefile': cookie_filepath,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            filepath = ydl.prepare_filename(info)

            if os.path.exists(filepath):
                logger.info(f"Video {url} already exists")
                return filepath
            ydl.download([url])
            return filepath
    except Exception as e:
        logger.exception(f"Failed to download video from {url} ({e})")

async def download_video_async(url: str, output_dir: str, cookie_filepath: str="cookies.txt") -> Coroutine:
    return await asyncio.to_thread(download_video, url, output_dir, cookie_filepath)

def get_urls_from_text(text: str) -> list[str]:
    ig_regexp = r'(?P<url>https://www.instagram.com/reel/[a-zA-Z0-9_-]+)' 
    urls = re.findall(ig_regexp, text)
    return urls