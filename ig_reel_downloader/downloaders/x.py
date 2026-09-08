from __future__ import annotations

import base64
import logging
import re
import urllib.request
from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    DownloadFailureReason,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    map_image_asset,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaItem

if TYPE_CHECKING:
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"(?P<url>https://(?:www\.)?(?:x\.com|twitter\.com)/[^\s<>()]+)",
    re.IGNORECASE,
)
SUPPORTED_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
TRAILING_PUNCTUATION = ".,;:!?\"')]/"
MAX_X_VIDEO_BYTES = 100_000_000
X_VIDEO_FORMAT = (
    f"best[filesize<={MAX_X_VIDEO_BYTES}]/best[filesize_approx<={MAX_X_VIDEO_BYTES}]"
)
MAX_X_IMAGE_BYTES = 20 * 1024 * 1024
MAX_X_PAGE_BYTES = 2 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
X_IMAGE_HOSTS = {"pbs.twimg.com"}
X_TOO_LARGE_MARKER = "x video exceeds the 100 mb size limit"
X_BLOCK_MARKERS = (
    "ip address is blocked from accessing this post",
    "sign in to confirm you're not a bot",
)


class XMediaTooLargeError(Exception):
    pass


class UnsupportedXMediaError(Exception):
    pass


class XDownloader:
    provider = "x"
    media_kind = "post"

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
        if parsed.netloc.lower() not in SUPPORTED_HOSTS:
            return None

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 3 or parts[1].lower() != "status":
            return None
        username, _, post_id = parts[:3]
        if not re.fullmatch(r"[a-zA-Z0-9_]+", username):
            return None
        if not post_id.isdigit():
            return None
        if len(parts) > 3 and not (
            len(parts) == 5
            and parts[3].lower() in {"photo", "video"}
            and parts[4].isdigit()
        ):
            return None

        normalized_url = f"https://x.com/{username}/status/{post_id}"
        return UrlCandidate(
            url=url,
            start=start,
            end=end,
            downloader=self,
            provider=self.provider,
            link_type=self.media_kind,
            normalized_url=normalized_url,
            local_ref=ProviderItemRef(self.provider, self.media_kind, post_id),
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
        ydl_opts["format"] = X_VIDEO_FORMAT
        ydl_opts["max_filesize"] = MAX_X_VIDEO_BYTES
        ydl_opts["progress_hooks"] = [_enforce_x_video_size]
        filepaths: list[str] = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info: _InfoDict = ydl.extract_info(url, download=False)
                asset_infos = _video_infos(info)
                for asset_info in asset_infos:
                    _enforce_known_x_video_size(asset_info)
                filepaths = [
                    ydl.prepare_filename(asset_info) for asset_info in asset_infos
                ]
                ydl.download([url])
                for filepath in filepaths:
                    downloaded_path = Path(filepath)
                    if not downloaded_path.is_file():
                        raise UnsupportedXMediaError(
                            "yt-dlp did not produce the expected X video file"
                        )
                    if downloaded_path.stat().st_size > MAX_X_VIDEO_BYTES:
                        raise XMediaTooLargeError(X_TOO_LARGE_MARKER)

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
                    map_video_asset(asset_info, filepath=filepath, asset_index=index)
                    for index, (asset_info, filepath) in enumerate(
                        zip(asset_infos, filepaths, strict=True)
                    )
                ],
                created_at=now,
                updated_at=now,
            )
            return MediaDownloadResult(media=media)
        except Exception as error:
            if _is_no_video_error(error):
                try:
                    return _download_non_video_post(url, ref, context)
                except Exception as fallback_error:
                    failure_reason = _classify_x_error(fallback_error)
                    logger.exception(
                        "Failed to download non-video X post %s (%s)",
                        url,
                        fallback_error,
                    )
                    return MediaDownloadResult(
                        media=None,
                        failure_reason=failure_reason,
                    )

            failure_reason = _classify_x_error(error)
            _remove_failed_x_downloads(context.output_dir, ref, filepaths)
            if failure_reason in ("auth", "blocked", "unsupported"):
                logger.warning(
                    "Failed to download X post from %s: %s (%s)",
                    url,
                    failure_reason,
                    error,
                )
            else:
                logger.exception("Failed to download X post from %s (%s)", url, error)
            return MediaDownloadResult(media=None, failure_reason=failure_reason)


def _video_infos(info: _InfoDict) -> list[_InfoDict]:
    entries = info.get("entries")
    if not isinstance(entries, list):
        return [info]
    video_infos = [
        cast("_InfoDict", entry) for entry in entries if isinstance(entry, Mapping)
    ]
    return video_infos or [info]


def _is_no_video_error(error: Exception) -> bool:
    return (
        isinstance(error, DownloadError)
        and "no video could be found" in str(error).lower()
    )


def _download_non_video_post(
    url: str,
    ref: ProviderItemRef,
    context: DownloadContext,
) -> MediaDownloadResult:
    page = _fetch_x_page(url)
    metadata = _XPageMetadataParser()
    metadata.feed(page)
    if not metadata.description and not metadata.image_urls:
        return MediaDownloadResult(media=None, failure_reason="unsupported")

    scoped_dir = (
        context.output_dir / ref.provider / ref.media_kind / ref.provider_item_id
    )
    if metadata.image_urls:
        scoped_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    for index, image_url in enumerate(metadata.image_urls):
        extension = _image_extension(image_url)
        filepath = scoped_dir / f"image-{index}.{extension}"
        _download_image_file(image_url, filepath)
        assets.append(
            map_image_asset(
                {"filesize": filepath.stat().st_size},
                filepath=str(filepath),
                asset_index=index,
            )
        )

    like_count = _extract_like_count(page, ref.provider_item_id)
    if like_count is None:
        logger.warning("Could not extract like count from X post %s", url)

    now = datetime.now()
    return MediaDownloadResult(
        media=MediaItem(
            id=ref.media_id,
            provider=ref.provider,
            media_kind=ref.media_kind,
            provider_item_id=ref.provider_item_id,
            original_url=url,
            title=metadata.title,
            description=metadata.description,
            metadata={
                "like_count": like_count or 0,
                "comments": [],
                "text_only": not assets,
            },
            assets=assets,
            created_at=now,
            updated_at=now,
        )
    )


def _enforce_known_x_video_size(info: Mapping[str, Any]) -> None:
    size = info.get("filesize") or info.get("filesize_approx")
    if not isinstance(size, int | float) or size <= 0:
        raise UnsupportedXMediaError("X video size is unavailable")
    if size > MAX_X_VIDEO_BYTES:
        raise XMediaTooLargeError(X_TOO_LARGE_MARKER)


def _enforce_x_video_size(progress: Mapping[str, Any]) -> None:
    size = progress.get("downloaded_bytes") or progress.get("total_bytes")
    if isinstance(size, int | float) and size > MAX_X_VIDEO_BYTES:
        raise XMediaTooLargeError(X_TOO_LARGE_MARKER)


def _extract_like_count(page: str, post_id: str) -> int | None:
    encoded_post_id = base64.b64encode(f"Tweet:{post_id}".encode()).decode()
    match = re.search(
        rf'{re.escape(encoded_post_id)}:counts".{{0,500}}?favorite_count:(\d+)',
        page,
        re.DOTALL,
    )
    return int(match.group(1)) if match is not None else None


def _classify_x_error(error: Exception) -> DownloadFailureReason:
    if isinstance(error, UnsupportedXMediaError) or _is_x_video_too_large_error(error):
        return "unsupported"
    if isinstance(error, DownloadError):
        message = str(error).lower()
        if any(marker in message for marker in X_BLOCK_MARKERS):
            return "blocked"
    return "unknown"


def _is_x_video_too_large_error(error: Exception) -> bool:
    return isinstance(error, XMediaTooLargeError) or (
        isinstance(error, DownloadError) and X_TOO_LARGE_MARKER in str(error).lower()
    )


def _remove_failed_x_downloads(
    output_dir: Path,
    ref: ProviderItemRef,
    filepaths: list[str],
) -> None:
    for filepath in filepaths:
        Path(filepath).unlink(missing_ok=True)

    scoped_dir = output_dir / ref.provider / ref.media_kind / ref.provider_item_id
    if not scoped_dir.is_dir():
        return
    for path in scoped_dir.rglob("*"):
        if path.is_file() and (".part" in path.name or path.suffix == ".ytdl"):
            path.unlink(missing_ok=True)


def _fetch_x_page(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_X_PAGE_BYTES:
            raise UnsupportedXMediaError("X page exceeds the size limit")
        content = cast("bytes", response.read(MAX_X_PAGE_BYTES + 1))
    if len(content) > MAX_X_PAGE_BYTES:
        raise UnsupportedXMediaError("X page exceeds the size limit")
    return content.decode("utf-8")


def _download_image_file(url: str, filepath: Path) -> None:
    if not _is_allowed_x_image_url(url):
        raise UnsupportedXMediaError("X image URL is not on the allowed CDN")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://x.com/"},
    )
    temporary_path = filepath.with_name(f"{filepath.name}.part")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if not _is_allowed_x_image_url(response.url):
                raise UnsupportedXMediaError(
                    "X image redirected away from the allowed CDN"
                )
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.lower().startswith("image/"):
                raise UnsupportedXMediaError(
                    f"expected an image response, got {content_type}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_X_IMAGE_BYTES:
                raise UnsupportedXMediaError("X image exceeds the size limit")

            bytes_written = 0
            with temporary_path.open("wb") as output_file:
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    bytes_written += len(chunk)
                    if bytes_written > MAX_X_IMAGE_BYTES:
                        raise UnsupportedXMediaError("X image exceeds the size limit")
                    output_file.write(chunk)
        temporary_path.replace(filepath)
    finally:
        temporary_path.unlink(missing_ok=True)


def _is_allowed_x_image_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() in X_IMAGE_HOSTS
        and port is None
        and parsed.username is None
        and parsed.password is None
    )


def _image_extension(url: str) -> str:
    match = re.search(
        r"\.(jpg|jpeg|png|webp)(?::[^/?]+)?(?:[?#]|$)", url, re.IGNORECASE
    )
    return match.group(1).lower() if match is not None else "jpg"


class _XPageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description: str | None = None
        self.image_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "meta":
            return
        attributes = dict(attrs)
        property_name = attributes.get("property")
        content = attributes.get("content")
        if content is None:
            return
        if property_name == "og:title":
            self.title = content
        elif property_name == "og:description":
            self.description = content
        elif (
            property_name == "og:image"
            and _is_allowed_x_image_url(content)
            and content not in self.image_urls
        ):
            self.image_urls.append(content)
