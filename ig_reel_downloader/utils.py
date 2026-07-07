import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yt_dlp.utils import DownloadError

from .repository import models

DownloadFailureReason = Literal["auth", "unsupported", "unknown"]


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


def download_video_result(
    url: str,
    output_dir: str,
    cookie_filepath: str = "cookies.txt",
) -> DownloadResult:
    from .downloaders.base import DownloadContext
    from .downloaders.instagram import InstagramReelDownloader

    downloader = InstagramReelDownloader(cookie_filepath=Path(cookie_filepath))
    result = downloader.download(url, DownloadContext(output_dir=Path(output_dir)))
    if result.media is None:
        return DownloadResult(reel=None, failure_reason=result.failure_reason)

    media = result.media
    return DownloadResult(
        reel=models.IgReel(
            id=media.provider_item_id,
            title=media.title,
            description=media.description,
            filepath=media.assets[0].filepath,
            url=media.original_url,
            comments=json.dumps(media.metadata.get("comments", [])),
            like_count=int(media.metadata.get("like_count") or 0),
            created_at=media.created_at,
        )
    )


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
    from .downloaders.instagram import InstagramReelDownloader

    return [match.url for match in InstagramReelDownloader().extract_urls(text)]


def get_id_from_url(url: str) -> str | None:
    from .downloaders.instagram import InstagramReelDownloader

    ref = InstagramReelDownloader().get_provider_item_ref(url)
    if ref is None:
        return None
    return ref.provider_item_id
