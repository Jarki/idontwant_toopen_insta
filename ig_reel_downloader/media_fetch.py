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
            return MediaFetchResult(
                media=cached, url=request.normalized_url or request.url
            )

        download_result = request.downloader.download(
            request,
            DownloadContext(output_dir=self.output_dir),
        )
        if download_result.media is None:
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

        self.repository.insert_media(download_result.media)
        return MediaFetchResult(
            media=download_result.media,
            url=request.normalized_url or request.url,
        )


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
