from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

import ig_reel_downloader.downloaders.tiktok_media as tiktok_media
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
from ig_reel_downloader.downloaders.tiktok_urls import (
    CANONICAL_POST_URL_PATTERN,
    SHARE_URL_PATTERN,
    normalize_tiktok_post_url,
    normalize_tiktok_share_url,
)
from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    build_metadata_ytdlp_options,
)
from ig_reel_downloader.repository.models import MediaItem

logger = logging.getLogger(__name__)

TIKTOK_MAX_ATTEMPTS = 3
TIKTOK_SHARE_RESOLVE_TIMEOUT_SECONDS = 10
TIKTOK_BLOCK_MARKERS = (
    "unexpected response from webpage request",
    "unable to extract universal data for rehydration",
    "your ip address is blocked from accessing this post",
)


class TikTokDownloader:
    provider = "tiktok"
    media_kind = "video"

    def __init__(
        self,
        cookie_filepath: Path | None = None,
        strategies: Mapping[str, tiktok_media.TikTokMediaStrategy] | None = None,
    ) -> None:
        self.cookie_filepath = cookie_filepath
        self.strategies = (
            tiktok_media.DEFAULT_TIKTOK_STRATEGIES if strategies is None else strategies
        )

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in CANONICAL_POST_URL_PATTERN.finditer(text):
            post_id = match.group("id")
            kind = match.group("kind")
            normalized_url = normalize_tiktok_post_url(
                username=match.group("username"),
                kind=kind,
                post_id=post_id,
            )
            candidates.append(
                UrlCandidate(
                    url=match.group("url"),
                    start=match.start("url"),
                    end=match.end("url"),
                    downloader=self,
                    provider=self.provider,
                    link_type=kind,
                    normalized_url=normalized_url,
                    local_ref=ProviderItemRef(self.provider, kind, post_id),
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
                    normalized_url=normalize_tiktok_share_url(
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
                    request = Request(
                        source_url,
                        method="HEAD",
                        extensions={"timeout": TIKTOK_SHARE_RESOLVE_TIMEOUT_SECONDS},
                    )
                    with cast("Any", ydl).urlopen(request) as response:
                        destination = response.url
                match = CANONICAL_POST_URL_PATTERN.fullmatch(destination)
                if match is None:
                    raise UnsupportedError(
                        "TikTok share link did not resolve to a canonical post"
                    )
                kind = match.group("kind")
                post_id = match.group("id")
                normalized_url = normalize_tiktok_post_url(
                    match.group("username"), kind, post_id
                )
                return ResolveResult(
                    request=ResolvedMediaRequest(
                        url=source_url,
                        downloader=self,
                        provider_item_ref=ProviderItemRef(self.provider, kind, post_id),
                        normalized_url=normalized_url,
                    )
                )
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

        raise AssertionError("TikTok resolution attempts exhausted without a result")

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

        strategy = self.strategies.get(ref.media_kind)
        if strategy is None:
            return MediaDownloadResult(media=None, failure_reason="unsupported")

        for attempt in range(1, TIKTOK_MAX_ATTEMPTS + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info: Mapping[str, Any] = (
                        request.info
                        if request.info is not None
                        else strategy.load_info(ydl, url)
                    )
                    assets = strategy.download_assets(ydl, info, url, ref, context)
                    now = datetime.now()
                    media = MediaItem(
                        id=ref.media_id,
                        provider=ref.provider,
                        media_kind=ref.media_kind,
                        provider_item_id=ref.provider_item_id,
                        original_url=url,
                        title=str(info.get("title") or ""),
                        description=cast("str | None", info.get("description")),
                        metadata=_tiktok_metadata(info),
                        assets=assets,
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
                        "Failed to download TikTok media from %s: %s (%s)",
                        url,
                        failure_reason,
                        error,
                    )
                elif failure_reason in ("auth", "unsupported"):
                    logger.warning(
                        "TikTok download failed: %s (%s)", failure_reason, error
                    )
                else:
                    logger.exception(
                        "Unexpected TikTok download failure",
                        extra={"event_code": "provider.tiktok.download_unexpected"},
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
    cause = (
        error.exc_info[1]
        if isinstance(error, DownloadError) and error.exc_info
        else error
    )
    if isinstance(cause, UnsupportedError):
        return "unsupported"
    if isinstance(cause, ExtractorError) and "login" in str(cause).lower():
        return "auth"
    if isinstance(error, DownloadError | ExtractorError):
        message = str(error).lower()
        if any(marker in message for marker in TIKTOK_BLOCK_MARKERS):
            return "blocked"
    return "unknown"
