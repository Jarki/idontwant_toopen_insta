from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yt_dlp

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    DownloadFailureReason,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
    UrlMatch,
)
from ig_reel_downloader.repository.models import MediaAsset, MediaItem
from ig_reel_downloader.utils import is_auth_required_download_error

if TYPE_CHECKING:
    from yt_dlp import _Params
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)
REEL_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.instagram\.com/reel/(?P<id>[a-zA-Z0-9_-]+))"
)


class InstagramReelDownloader:
    provider = "instagram"
    media_kind = "reel"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in REEL_URL_PATTERN.finditer(text):
            url = match.group("url")
            ref = ProviderItemRef(
                provider=self.provider,
                media_kind=self.media_kind,
                provider_item_id=match.group("id"),
            )
            candidates.append(
                UrlCandidate(
                    url=url,
                    start=match.start("url"),
                    end=match.end("url"),
                    downloader=self,
                    provider=self.provider,
                    link_type=self.media_kind,
                    normalized_url=url,
                    local_ref=ref,
                )
            )
        return candidates

    def extract_urls(self, text: str) -> list[UrlMatch]:
        return [
            UrlMatch(
                url=candidate.url,
                start=candidate.start,
                end=candidate.end,
                downloader=candidate.downloader,
                normalized_url=candidate.normalized_url,
            )
            for candidate in self.extract_candidates(text)
        ]

    def get_provider_item_ref(self, url: str) -> ProviderItemRef | None:
        match = REEL_URL_PATTERN.search(url)
        if match is None:
            return None
        return ProviderItemRef(
            provider=self.provider,
            media_kind=self.media_kind,
            provider_item_id=match.group("id"),
        )

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if candidate.local_ref is None:
            return ResolveResult(request=None, failure_reason="unsupported")
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=candidate.normalized_url or candidate.url,
                downloader=self,
                provider_item_ref=candidate.local_ref,
                normalized_url=candidate.normalized_url,
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest | str,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        if isinstance(request, str):
            url = request
            ref = self.get_provider_item_ref(url)
            if ref is None:
                return MediaDownloadResult(media=None, failure_reason="unsupported")
        else:
            url = request.normalized_url or request.url
            ref = request.provider_item_ref

        ydl_opts: _Params = {
            "outtmpl": str(context.output_dir / "%(id)s.%(ext)s"),
            "format": "best",
            "quiet": True,
        }
        if self.cookie_filepath is not None and self.cookie_filepath.exists():
            ydl_opts["cookiefile"] = str(self.cookie_filepath)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info: _InfoDict = ydl.extract_info(url, download=False)
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
                    assets=[
                        MediaAsset(
                            asset_index=0,
                            asset_type="video",
                            filepath=filepath,
                            width=_optional_int(info.get("width")),
                            height=_optional_int(info.get("height")),
                            duration_seconds=_optional_float(info.get("duration")),
                            file_size_bytes=_optional_int(
                                info.get("filesize") or info.get("filesize_approx")
                            ),
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
                return MediaDownloadResult(media=media)
        except Exception as error:
            failure_reason: DownloadFailureReason = (
                "auth" if is_auth_required_download_error(error) else "unknown"
            )
            if failure_reason == "auth":
                logger.warning(
                    "Failed to download video from %s: authentication required (%s)",
                    url,
                    error,
                )
            else:
                logger.exception("Failed to download video from %s (%s)", url, error)
            return MediaDownloadResult(media=None, failure_reason=failure_reason)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float | str):
        return float(value)
    return None
