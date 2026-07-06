import asyncio
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yt_dlp

if TYPE_CHECKING:
    from yt_dlp import _Params
    from yt_dlp.extractor.common import _InfoDict

from .repository import models

logger = logging.getLogger(__name__)


def download_video(
    url: str,
    output_dir: str,
    cookie_filepath: str = "cookies.txt",
) -> models.IgReel | None:
    ydl_opts: _Params = {
        "outtmpl": str(Path(output_dir) / "%(id)s.%(ext)s"),
        "format": "best",
        "quiet": True,
        "cookiefile": cookie_filepath,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info: _InfoDict = ydl.extract_info(url, download=False)
            filepath = ydl.prepare_filename(info)
            ydl.download([url])
            return models.IgReel(
                id=info["id"],
                title=str(info.get("title") or ""),
                description=info.get("description"),
                filepath=filepath,
                like_count=int(info.get("like_count") or 0),
                url=url,
                comments=json.dumps(info.get("comments", [])),
            )
    except Exception as e:
        logger.exception("Failed to download video from %s (%s)", url, e)
        return None


async def download_video_async(
    url: str,
    output_dir: str,
    cookie_filepath: str = "cookies.txt",
) -> models.IgReel | None:
    return await asyncio.to_thread(download_video, url, output_dir, cookie_filepath)


def get_urls_from_text(text: str) -> list[str]:
    ig_regexp = r"(?P<url>https://www.instagram.com/reel/[a-zA-Z0-9_-]+)"
    urls = re.findall(ig_regexp, text)
    return urls


def get_id_from_url(url: str) -> str | None:
    ig_regexp = r"https://www.instagram.com/reel/(?P<id>[a-zA-Z0-9_-]+)"
    match = re.search(ig_regexp, url)
    if match:
        return match.group("id")
    return None


def ig_reel_model_factory(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
) -> models.IgReel:
    fields = [col[0] for col in cursor.description]
    return models.IgReel(**dict(zip(fields, row, strict=True)))
