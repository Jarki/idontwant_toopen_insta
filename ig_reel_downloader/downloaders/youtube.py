from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    build_metadata_ytdlp_options,
    classify_download_error,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaItem

if TYPE_CHECKING:
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"(?P<url>https://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s<>()]+)",
    re.IGNORECASE,
)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORTS_PATH_PREFIX = "/shorts/"
MAX_VIDEO_DURATION_SECONDS = 60
TRAILING_PUNCTUATION = ".,;:!?\"')]/"


class YouTubeDownloader:
    provider = "youtube"
    media_kind = "video"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in URL_PATTERN.finditer(text):
            raw_url = match.group("url").rstrip(TRAILING_PUNCTUATION)
            candidate = self._candidate_from_url(
                raw_url,
                start=match.start("url"),
                end=match.start("url") + len(raw_url),
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate_from_url(
        self,
        url: str,
        *,
        start: int,
        end: int,
    ) -> UrlCandidate | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        canonical_host = "www.youtube.com" if host == "m.youtube.com" else host

        if canonical_host == "youtu.be":
            parts = _path_parts(parsed.path)
            if len(parts) != 1:
                return None
            video_id = parts[0]
            if not _is_valid_video_id(video_id):
                return None
            return UrlCandidate(
                url=url,
                start=start,
                end=end,
                downloader=self,
                provider=self.provider,
                link_type="video",
                normalized_url=f"https://youtu.be/{video_id}",
                local_ref=ProviderItemRef(self.provider, "video", video_id),
            )

        if canonical_host not in YOUTUBE_HOSTS:
            return None

        parts = _path_parts(parsed.path)
        if len(parts) == 2 and parts[0] == "shorts":
            short_id = parts[1]
            if not _is_valid_video_id(short_id):
                return None
            return UrlCandidate(
                url=url,
                start=start,
                end=end,
                downloader=self,
                provider=self.provider,
                link_type="short",
                normalized_url=f"https://{canonical_host}/shorts/{short_id}",
                local_ref=ProviderItemRef(self.provider, "short", short_id),
            )

        if parsed.path.rstrip("/") != "/watch":
            return None
        watch_video_id = _watch_video_id(parsed.query)
        if watch_video_id is None:
            return None
        return UrlCandidate(
            url=url,
            start=start,
            end=end,
            downloader=self,
            provider=self.provider,
            link_type="video",
            normalized_url=f"https://{canonical_host}/watch?v={watch_video_id}",
            local_ref=ProviderItemRef(self.provider, "video", watch_video_id),
        )

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if candidate.local_ref is None:
            return ResolveResult(request=None, failure_reason="unsupported")

        source_url = candidate.normalized_url or candidate.url
        if candidate.link_type == "short":
            return ResolveResult(
                request=ResolvedMediaRequest(
                    url=source_url,
                    downloader=self,
                    provider_item_ref=candidate.local_ref,
                    normalized_url=candidate.normalized_url,
                )
            )

        if candidate.link_type != "video":
            return ResolveResult(request=None, failure_reason="unsupported")

        ydl_opts = build_metadata_ytdlp_options(cookie_filepath=self.cookie_filepath)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info: _InfoDict = ydl.extract_info(source_url, download=False)
        except Exception as error:
            raise ResolutionError(source_url, classify_download_error(error)) from error

        duration = _duration_seconds(info.get("duration"))
        if duration is not None and duration > MAX_VIDEO_DURATION_SECONDS:
            return ResolveResult(request=None, skipped=True)

        return ResolveResult(
            request=ResolvedMediaRequest(
                url=source_url,
                downloader=self,
                provider_item_ref=candidate.local_ref,
                normalized_url=candidate.normalized_url,
                info=cast("dict[str, object]", info),
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        url = request.normalized_url or request.url
        ref = request.provider_item_ref
        ydl_opts = build_download_ytdlp_options(
            output_dir=context.output_dir,
            cookie_filepath=self.cookie_filepath,
            provider=ref.provider,
            media_kind=ref.media_kind,
            provider_item_id=ref.provider_item_id,
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = (
                    cast("_InfoDict", request.info)
                    if request.info is not None
                    else ydl.extract_info(url, download=False)
                )
                filepath = ydl.prepare_filename(info)
                ydl.download([url])
                now = datetime.now()
                media = MediaItem(
                    id=ref.media_id,
                    provider=ref.provider,
                    media_kind=ref.media_kind,
                    provider_item_id=ref.provider_item_id,
                    original_url=url,
                    title=str(info.get("title") or ""),
                    description=info.get("description"),
                    metadata={
                        "like_count": int(info.get("like_count") or 0),
                        "comments": info.get("comments", []),
                    },
                    assets=[map_video_asset(info, filepath=filepath)],
                    created_at=now,
                    updated_at=now,
                )
                return MediaDownloadResult(media=media)
        except Exception as error:
            failure_reason = classify_download_error(error)
            if failure_reason == "auth":
                logger.warning(
                    "Failed to download YouTube video from %s: authentication required (%s)",
                    url,
                    error,
                )
            else:
                logger.exception(
                    "Failed to download YouTube video from %s (%s)", url, error
                )
            return MediaDownloadResult(media=None, failure_reason=failure_reason)


def _path_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _watch_video_id(query: str) -> str | None:
    values = parse_qs(query).get("v")
    if not values:
        return None
    video_id = values[0]
    if not _is_valid_video_id(video_id):
        return None
    return video_id


def _is_valid_video_id(video_id: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", video_id))


def _duration_seconds(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float | str):
        return float(value)
    return None
