import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yt_dlp
from yt_dlp.utils import DownloadError

if TYPE_CHECKING:
    from yt_dlp import _Params
    from yt_dlp.extractor.common import _InfoDict

from .repository import models

logger = logging.getLogger(__name__)

DownloadFailureReason = Literal["auth", "unknown"]


@dataclass(frozen=True)
class DownloadResult:
    reel: models.IgReel | None
    failure_reason: DownloadFailureReason | None = None


def is_auth_required_download_error(error: Exception) -> bool:
    message = str(error)
    return (
        isinstance(error, DownloadError)
        and "Instagram sent an empty media response" in message
        and "--cookies" in message
    )


def _get_download_failure_reason(error: Exception) -> DownloadFailureReason:
    if is_auth_required_download_error(error):
        return "auth"
    return "unknown"


def download_video_result(
    url: str,
    output_dir: str,
    cookie_filepath: str = "cookies.txt",
) -> DownloadResult:
    ydl_opts: _Params = {
        "outtmpl": str(Path(output_dir) / "%(id)s.%(ext)s"),
        "format": "best",
        "quiet": True,
    }
    if Path(cookie_filepath).exists():
        ydl_opts["cookiefile"] = cookie_filepath

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info: _InfoDict = ydl.extract_info(url, download=False)

            filepath = ydl.prepare_filename(info)
            ydl.download([url])
            return DownloadResult(
                reel=models.IgReel(
                    id=info["id"],
                    title=str(info.get("title") or ""),
                    description=info.get("description"),
                    filepath=filepath,
                    like_count=int(info.get("like_count") or 0),
                    url=url,
                    comments=json.dumps(info.get("comments", [])),
                )
            )
    except Exception as e:
        failure_reason = _get_download_failure_reason(e)
        if failure_reason == "auth":
            logger.warning(
                "Failed to download video from %s: authentication required (%s)", url, e
            )
        else:
            logger.exception("Failed to download video from %s (%s)", url, e)
        return DownloadResult(reel=None, failure_reason=failure_reason)


def download_video(
    url: str,
    output_dir: str,
    cookie_filepath: str = "cookies.txt",
) -> models.IgReel | None:
    return download_video_result(url, output_dir, cookie_filepath).reel


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
