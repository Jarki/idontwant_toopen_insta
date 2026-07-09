from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    DownloadFailureReason,
    ProviderItemRef,
    ResolutionError,
    UrlCandidate,
)
from ig_reel_downloader.repository.base import Repository
from ig_reel_downloader.repository.models import MediaItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaFetchResult:
    media: MediaItem | None
    url: str
    failure_reason: DownloadFailureReason | None = None
    skipped: bool = False


class MediaFetchService:
    def __init__(self, repository: Repository, output_dir: Path) -> None:
        self.repository = repository
        self.output_dir = output_dir

    def fetch(self, candidate: UrlCandidate) -> MediaFetchResult:
        result_url = candidate.normalized_url or candidate.url
        try:
            resolve_result = candidate.downloader.resolve(candidate)
        except ResolutionError as error:
            return MediaFetchResult(
                media=None,
                url=error.url,
                failure_reason=error.failure_reason,
            )
        if resolve_result.skipped:
            return MediaFetchResult(media=None, url=result_url, skipped=True)
        if resolve_result.request is None:
            return MediaFetchResult(
                media=None,
                url=result_url,
                failure_reason=resolve_result.failure_reason or "unknown",
            )

        request = resolve_result.request
        ref = request.provider_item_ref
        cached = self.repository.get_media_by_provider_item(
            ref.provider,
            ref.media_kind,
            ref.provider_item_id,
        )
        if cached is not None and _is_reusable(cached):
            logger.info(
                "Cache hit: %s:%s %s",
                cached.provider,
                cached.media_kind,
                cached.provider_item_id,
            )
            return MediaFetchResult(
                media=cached, url=request.normalized_url or request.url
            )

        logger.info(
            "Downloading %s:%s %s...",
            ref.provider,
            ref.media_kind,
            ref.provider_item_id,
        )
        download_result = request.downloader.download(
            request,
            DownloadContext(output_dir=self.output_dir),
        )
        if download_result.media is None:
            logger.warning(
                "Download failed for %s:%s %s: %s",
                ref.provider,
                ref.media_kind,
                ref.provider_item_id,
                download_result.failure_reason or "unknown",
            )
            return MediaFetchResult(
                media=None,
                url=request.normalized_url or request.url,
                failure_reason=download_result.failure_reason or "unknown",
            )

        if not _identity_matches(download_result.media, ref):
            logger.error(
                "Downloader returned identity mismatch for %s: expected %s got %s:%s:%s",
                request.normalized_url or request.url,
                ref.media_id,
                download_result.media.provider,
                download_result.media.media_kind,
                download_result.media.provider_item_id,
            )
            return MediaFetchResult(
                media=None,
                url=request.normalized_url or request.url,
                failure_reason="unknown",
            )

        duration_str = _duration_str(download_result.media)
        logger.info(
            "Downloaded %s:%s %s%s",
            download_result.media.provider,
            download_result.media.media_kind,
            download_result.media.provider_item_id,
            f" ({duration_str})" if duration_str else "",
        )
        self.repository.insert_media(download_result.media)
        return MediaFetchResult(
            media=download_result.media,
            url=request.normalized_url or request.url,
        )


def _duration_str(media: MediaItem) -> str:
    parts = []
    for asset in media.assets:
        if asset.duration_seconds is not None:
            parts.append(f"{asset.asset_type}={asset.duration_seconds}s")
        elif asset.file_size_bytes is not None:
            parts.append(f"{asset.asset_type}={_format_size(asset.file_size_bytes)}")
    return ", ".join(parts)


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f}MiB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KiB"
    return f"{size_bytes}B"


def _is_reusable(media: MediaItem) -> bool:
    if not media.assets:
        return False
    return all(Path(asset.filepath).is_file() for asset in media.assets)


def _identity_matches(media: MediaItem, ref: ProviderItemRef) -> bool:
    return (
        media.provider == ref.provider
        and media.media_kind == ref.media_kind
        and media.provider_item_id == ref.provider_item_id
        and media.id == ref.media_id
    )
