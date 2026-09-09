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

    def fetch(
        self,
        candidate: UrlCandidate,
        media_request_id: int,
    ) -> MediaFetchResult:
        result_url = candidate.normalized_url or candidate.url
        try:
            resolve_result = candidate.downloader.resolve(candidate)
        except ResolutionError as error:
            self._record_failure(
                media_request_id,
                candidate,
                error.url,
                error.failure_reason,
            )
            return MediaFetchResult(
                media=None,
                url=error.url,
                failure_reason=error.failure_reason,
            )
        except Exception:
            logger.exception("Unexpected error while resolving %s", result_url)
            self._record_failure(
                media_request_id,
                candidate,
                result_url,
                "unknown",
            )
            return MediaFetchResult(
                media=None,
                url=result_url,
                failure_reason="unknown",
            )
        if resolve_result.skipped:
            self._record_failure(
                media_request_id,
                candidate,
                result_url,
                "unsupported",
            )
            return MediaFetchResult(media=None, url=result_url, skipped=True)
        if resolve_result.request is None:
            failure_reason = resolve_result.failure_reason or "unknown"
            self._record_failure(
                media_request_id,
                candidate,
                result_url,
                failure_reason,
            )
            return MediaFetchResult(
                media=None,
                url=result_url,
                failure_reason=failure_reason,
            )

        request = resolve_result.request
        ref = request.provider_item_ref
        cached = self.repository.get_media_by_provider_item(
            ref.provider,
            ref.media_kind,
            ref.provider_item_id,
        )
        if cached is not None and _is_reusable(cached):
            logger.debug(
                "Cache hit: %s:%s %s",
                cached.provider,
                cached.media_kind,
                cached.provider_item_id,
            )
            self.repository.mark_media_request_succeeded(
                media_request_id,
                cached.id,
            )
            return MediaFetchResult(
                media=cached, url=request.normalized_url or request.url
            )

        logger.debug(
            "Downloading %s:%s %s...",
            ref.provider,
            ref.media_kind,
            ref.provider_item_id,
        )
        try:
            download_result = request.downloader.download(
                request,
                DownloadContext(output_dir=self.output_dir),
            )
        except Exception:
            failure_url = request.normalized_url or request.url
            logger.exception(
                "Unexpected error while downloading %s:%s %s",
                ref.provider,
                ref.media_kind,
                ref.provider_item_id,
            )
            self._record_failure(
                media_request_id,
                candidate,
                failure_url,
                "unknown",
                ref,
            )
            return MediaFetchResult(
                media=None,
                url=failure_url,
                failure_reason="unknown",
            )
        if download_result.media is None:
            failure_reason = download_result.failure_reason or "unknown"
            failure_url = request.normalized_url or request.url
            logger.warning(
                "Download failed for %s:%s %s: %s",
                ref.provider,
                ref.media_kind,
                ref.provider_item_id,
                failure_reason,
            )
            self._record_failure(
                media_request_id,
                candidate,
                failure_url,
                failure_reason,
                ref,
            )
            return MediaFetchResult(
                media=None,
                url=failure_url,
                failure_reason=failure_reason,
            )

        if not _identity_matches(download_result.media, ref):
            failure_url = request.normalized_url or request.url
            logger.error(
                "Downloader returned identity mismatch for %s: expected %s got %s:%s:%s",
                failure_url,
                ref.media_id,
                download_result.media.provider,
                download_result.media.media_kind,
                download_result.media.provider_item_id,
            )
            self._record_failure(
                media_request_id,
                candidate,
                failure_url,
                "unknown",
                ref,
            )
            return MediaFetchResult(
                media=None,
                url=failure_url,
                failure_reason="unknown",
            )

        duration_str = _duration_str(download_result.media)
        logger.debug(
            "Downloaded %s:%s %s%s",
            download_result.media.provider,
            download_result.media.media_kind,
            download_result.media.provider_item_id,
            f" ({duration_str})" if duration_str else "",
        )
        self.repository.insert_media_for_request(
            media_request_id,
            download_result.media,
        )
        return MediaFetchResult(
            media=download_result.media,
            url=request.normalized_url or request.url,
        )

    def _record_failure(
        self,
        media_request_id: int,
        candidate: UrlCandidate,
        url: str,
        failure_reason: DownloadFailureReason,
        ref: ProviderItemRef | None = None,
    ) -> None:
        identity = ref or candidate.local_ref
        self.repository.mark_media_request_failed(
            media_request_id,
            failure_reason,
            url,
            identity.provider_item_id if identity is not None else None,
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
        if media.metadata.get("text_only") is not True:
            return False
        return not (
            media.provider == "x"
            and media.description is not None
            and media.description.endswith("…")
            and media.metadata.get("description_complete") is not True
        )
    return all(Path(asset.filepath).is_file() for asset in media.assets)


def _identity_matches(media: MediaItem, ref: ProviderItemRef) -> bool:
    return (
        media.provider == ref.provider
        and media.media_kind == ref.media_kind
        and media.provider_item_id == ref.provider_item_id
        and media.id == ref.media_id
    )
