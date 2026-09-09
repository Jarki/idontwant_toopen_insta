from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yt_dlp
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    DownloadFailureReason,
    MediaDownloadResult,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.downloaders.provider_metadata import (
    normalize_provider_metadata,
)
from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    build_metadata_ytdlp_options,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaItem

if TYPE_CHECKING:
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)

TIKTOK_MAX_ATTEMPTS = 3
TIKTOK_BLOCK_MARKERS = (
    "unexpected response from webpage request",
    "unable to extract universal data for rehydration",
)

CANONICAL_VIDEO_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.tiktok\.com/@(?P<username>[a-zA-Z0-9._-]+)"
    r"/video/(?P<id>\d+)(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)
SHARE_URL_PATTERN = re.compile(
    r"(?P<url>https://(?P<host>v[mt]\.tiktok\.com)/(?P<id>[a-zA-Z0-9_-]+)"
    r"(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)


def _normalize_tiktok_video_url(username: str, video_id: str) -> str:
    return f"https://www.tiktok.com/@{username}/video/{video_id}"


def _normalize_tiktok_share_url(host: str, share_id: str) -> str:
    return f"https://{host}/{share_id}/"


class TikTokDownloader:
    provider = "tiktok"
    media_kind = "video"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in CANONICAL_VIDEO_URL_PATTERN.finditer(text):
            video_id = match.group("id")
            normalized_url = _normalize_tiktok_video_url(
                username=match.group("username"),
                video_id=video_id,
            )
            candidates.append(
                UrlCandidate(
                    url=match.group("url"),
                    start=match.start("url"),
                    end=match.end("url"),
                    downloader=self,
                    provider=self.provider,
                    link_type=self.media_kind,
                    normalized_url=normalized_url,
                    local_ref=ProviderItemRef(self.provider, self.media_kind, video_id),
                )
            )

        for match in SHARE_URL_PATTERN.finditer(text):
            candidates.append(
                UrlCandidate(
                    url=match.group("url"),
                    start=match.start("url"),
                    end=match.end("url"),
                    downloader=self,
                    provider=self.provider,
                    link_type="share",
                    normalized_url=_normalize_tiktok_share_url(
                        host=match.group("host"),
                        share_id=match.group("id"),
                    ),
                    local_ref=None,
                )
            )

        return sorted(candidates, key=lambda candidate: candidate.start)

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if candidate.local_ref is not None:
            return ResolveResult(
                request=ResolvedMediaRequest(
                    url=candidate.normalized_url or candidate.url,
                    downloader=self,
                    provider_item_ref=candidate.local_ref,
                    normalized_url=candidate.normalized_url,
                )
            )

        if candidate.link_type != "share":
            return ResolveResult(request=None, failure_reason="unsupported")

        source_url = candidate.url
        display_url = candidate.normalized_url or candidate.url
        ydl_opts = build_metadata_ytdlp_options(cookie_filepath=self.cookie_filepath)
        for attempt in range(1, TIKTOK_MAX_ATTEMPTS + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info: _InfoDict = ydl.extract_info(source_url, download=False)
                break
            except Exception as error:
                failure_reason = _classify_tiktok_error(error)
                if failure_reason == "blocked" and attempt < TIKTOK_MAX_ATTEMPTS:
                    logger.warning(
                        "TikTok blocked share-link resolution for %s; retrying (%d/%d)",
                        display_url,
                        attempt,
                        TIKTOK_MAX_ATTEMPTS,
                    )
                    continue
                raise ResolutionError(display_url, failure_reason) from error

        video_id_value = info.get("id")
        if video_id_value is None:
            raise ResolutionError(display_url, "unknown")
        video_id = str(video_id_value)
        if not video_id:
            raise ResolutionError(display_url, "unknown")

        webpage_url = info.get("webpage_url")
        normalized_url = webpage_url if isinstance(webpage_url, str) else display_url
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=source_url,
                downloader=self,
                provider_item_ref=ProviderItemRef(
                    self.provider, self.media_kind, video_id
                ),
                normalized_url=normalized_url,
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

        for attempt in range(1, TIKTOK_MAX_ATTEMPTS + 1):
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
                        metadata=_tiktok_metadata(info),
                        assets=[map_video_asset(info, filepath=filepath)],
                        created_at=now,
                        updated_at=now,
                    )
                    return MediaDownloadResult(media=media)
            except Exception as error:
                failure_reason = _classify_tiktok_error(error)
                if failure_reason == "blocked" and attempt < TIKTOK_MAX_ATTEMPTS:
                    logger.warning(
                        "TikTok blocked download for %s; retrying (%d/%d)",
                        url,
                        attempt,
                        TIKTOK_MAX_ATTEMPTS,
                    )
                    continue
                if failure_reason == "blocked":
                    logger.warning(
                        "Failed to download TikTok video from %s: %s (%s)",
                        url,
                        failure_reason,
                        error,
                    )
                else:
                    logger.exception(
                        "Failed to download TikTok video from %s (%s)", url, error
                    )
                return MediaDownloadResult(media=None, failure_reason=failure_reason)

        raise AssertionError("TikTok download attempts exhausted without a result")


def _tiktok_metadata(info: Mapping[str, object]) -> dict[str, object]:
    return normalize_provider_metadata(
        info,
        counters=(
            "view_count",
            "like_count",
            "comment_count",
            "repost_count",
            "save_count",
        ),
        strings=("uploader", "channel", "track", "album"),
        string_lists=("artists",),
        numbers=("timestamp",),
    )


def _classify_tiktok_error(error: Exception) -> DownloadFailureReason:
    if isinstance(error, DownloadError):
        message = str(error).lower()
        if any(marker in message for marker in TIKTOK_BLOCK_MARKERS):
            return "blocked"
    return "unknown"
