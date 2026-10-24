from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit
from urllib.request import Request

import yt_dlp

# The upstream extractor module has no types-yt-dlp stub.
from yt_dlp.extractor.tiktok import TikTokIE  # type: ignore[import-not-found]
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

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
    map_image_asset,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaAsset, MediaItem

if TYPE_CHECKING:
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)

TIKTOK_MAX_ATTEMPTS = 3
MAX_TIKTOK_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
TIKTOK_BLOCK_MARKERS = (
    "unexpected response from webpage request",
    "unable to extract universal data for rehydration",
)

CANONICAL_POST_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.tiktok\.com/@(?P<username>[a-zA-Z0-9._-]+)"
    r"/(?P<kind>video|photo)/(?P<id>\d+)(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)
SHARE_URL_PATTERN = re.compile(
    r"(?P<url>https://(?P<host>v[mt]\.tiktok\.com)/(?P<id>[a-zA-Z0-9_-]+)"
    r"(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)


def _normalize_tiktok_post_url(username: str, kind: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{username}/{kind}/{post_id}"


def _normalize_tiktok_share_url(host: str, share_id: str) -> str:
    return f"https://{host}/{share_id}/"


class TikTokDownloader:
    provider = "tiktok"
    media_kind = "video"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in CANONICAL_POST_URL_PATTERN.finditer(text):
            post_id = match.group("id")
            kind = match.group("kind")
            normalized_url = _normalize_tiktok_post_url(
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
                    try:
                        info: _InfoDict = ydl.extract_info(source_url, download=False)
                    except DownloadError as error:
                        photo_url = _unsupported_photo_url(error)
                        if photo_url is None:
                            raise
                        info = cast("_InfoDict", _extract_photo_info(ydl, photo_url))
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
                    self.provider, "photo" if "images" in info else "video", video_id
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
                    info: Mapping[str, Any]
                    if ref.media_kind == "photo":
                        info = (
                            request.info
                            if request.info is not None
                            else _extract_photo_info(ydl, url)
                        )
                        assets = _download_photo_assets(ydl, info, ref, context)
                    else:
                        video_info = (
                            cast("_InfoDict", request.info)
                            if request.info is not None
                            else ydl.extract_info(url, download=False)
                        )
                        info = video_info
                        filepath = ydl.prepare_filename(video_info)
                        ydl.download([url])
                        assets = [map_video_asset(info, filepath=filepath)]
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


def _unsupported_photo_url(error: DownloadError) -> str | None:
    cause = error.exc_info[1] if error.exc_info else None
    if isinstance(cause, UnsupportedError):
        match = CANONICAL_POST_URL_PATTERN.fullmatch(cause.url)
        if match is not None and match.group("kind") == "photo":
            return _normalize_tiktok_post_url(
                match.group("username"), "photo", match.group("id")
            )
    return None


def _extract_photo_info(ydl: yt_dlp.YoutubeDL, url: str) -> dict[str, Any]:
    match = CANONICAL_POST_URL_PATTERN.fullmatch(url)
    if match is None or match.group("kind") != "photo":
        raise UnsupportedError(url)
    post_id = match.group("id")
    web_url = _normalize_tiktok_post_url(match.group("username"), "video", post_id)
    # The public extractor only returns slideshow audio. Keep the private raw
    # web-data adapter here so upstream changes cannot leak into other providers.
    extractor = cast("Any", TikTokIE(ydl))
    item, status = extractor._extract_web_data_and_status(web_url, post_id)
    if status != 0 or not isinstance(item, dict) or str(item.get("id")) != post_id:
        raise UnsupportedError(f"TikTok photo post unavailable, status code {status}")
    images = item.get("imagePost", {}).get("images")
    if not isinstance(images, list) or not images:
        raise UnsupportedError(url)
    info: dict[str, Any] = extractor._parse_aweme_video_web(
        item, url, post_id, extract_flat=True
    )
    info.update({"webpage_url": url, "images": images})
    return info


def _validate_image_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or not any(
            parsed.hostname.endswith(f".{domain}")
            for domain in (
                "tiktokcdn.com",
                "tiktokcdn-us.com",
                "tiktokcdn-eu.com",
                "byteoversea.com",
                "ibytedtos.com",
            )
        )
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UnsupportedError("TikTok image is not on an allowed CDN")


def _download_photo_assets(
    ydl: yt_dlp.YoutubeDL,
    info: Mapping[str, Any],
    ref: ProviderItemRef,
    context: DownloadContext,
) -> list[MediaAsset]:
    images = info.get("images")
    if not isinstance(images, list) or not images:
        raise UnsupportedError("TikTok photo post has no images")
    scoped_dir = (
        context.output_dir / ref.provider / ref.media_kind / ref.provider_item_id
    )
    scoped_dir.mkdir(parents=True, exist_ok=True)
    assets: list[MediaAsset] = []
    for index, image in enumerate(images):
        image_url = image["imageURL"]["urlList"][0]
        _validate_image_url(image_url)
        with ydl.urlopen(Request(image_url)) as response:
            _validate_image_url(response.url)
            content_type = (
                response.headers.get("Content-Type", "").split(";")[0].lower()
            )
            extension = {
                "image/jpeg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
            }.get(content_type)
            if extension is None:
                raise UnsupportedError(
                    "TikTok image returned an unsupported content type"
                )
            filepath = scoped_dir / f"{ref.provider_item_id}-{index}.{extension}"
            temporary_path = filepath.with_suffix(filepath.suffix + ".part")
            size = 0
            try:
                with temporary_path.open("wb") as output:
                    while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        if size > MAX_TIKTOK_IMAGE_BYTES:
                            raise UnsupportedError(
                                "TikTok image exceeds the size limit"
                            )
                        output.write(chunk)
                if size == 0:
                    raise UnsupportedError("TikTok returned an empty image")
                temporary_path.replace(filepath)
            finally:
                temporary_path.unlink(missing_ok=True)
        assets.append(
            map_image_asset(
                {
                    "width": image.get("imageWidth"),
                    "height": image.get("imageHeight"),
                    "filesize": size,
                },
                filepath=str(filepath),
                asset_index=index,
            )
        )
    return assets


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
