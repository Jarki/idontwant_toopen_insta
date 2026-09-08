from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast
from urllib.parse import ParseResult, urlparse
from urllib.request import Request

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

URL_PATTERN = re.compile(
    r"(?P<url>https://(?:www\.|old\.|m\.)?reddit\.com/[^\s<>()]+)",
    re.IGNORECASE,
)
CANONICAL_PATH_PATTERN = re.compile(
    r"^/(?:r/[^/]+/|user/[^/]+/)?comments/(?P<id>[a-zA-Z0-9]+)(?:/|$)",
    re.IGNORECASE,
)
SHARE_PATH_PATTERN = re.compile(
    r"^/r/[^/]+/s/[a-zA-Z0-9_-]+(?:/|$)",
    re.IGNORECASE,
)
TRAILING_PUNCTUATION = ".,;:!?\"')]}"
IMAGE_EXTENSIONS = {"gif", "jpeg", "jpg", "png", "webp"}
REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "m.reddit.com"}
REDDIT_IMAGE_HOSTS = {"i.redd.it", "preview.redd.it", "external-preview.redd.it"}
MAX_REDDIT_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
REDDIT_AUTH_MARKERS = (
    "account authentication is required",
    "an account that has opted in is required",
    "an account that has been approved is required",
)
REDDIT_BLOCK_MARKERS = ("ip address is blocked from accessing this post",)


class ImageCandidate(TypedDict):
    url: str
    width: NotRequired[object]
    height: NotRequired[object]


class RedditAuthenticationRequiredError(Exception):
    pass


class RedditMetadataError(Exception):
    pass


class UnsupportedRedditMediaError(Exception):
    pass


class RedditDownloader:
    provider = "reddit"
    media_kind = "post"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in URL_PATTERN.finditer(text):
            url = match.group("url").rstrip(TRAILING_PUNCTUATION)
            post_id = _post_id_from_url(url)
            if post_id is not None:
                normalized_url = _canonical_post_url(post_id)
                candidates.append(
                    UrlCandidate(
                        url=url,
                        start=match.start("url"),
                        end=match.start("url") + len(url),
                        downloader=self,
                        provider=self.provider,
                        link_type=self.media_kind,
                        normalized_url=normalized_url,
                        local_ref=ProviderItemRef(
                            self.provider, self.media_kind, post_id
                        ),
                    )
                )
            elif SHARE_PATH_PATTERN.match(urlparse(url).path) is not None:
                candidates.append(
                    UrlCandidate(
                        url=url,
                        start=match.start("url"),
                        end=match.start("url") + len(url),
                        downloader=self,
                        provider=self.provider,
                        link_type="share",
                        normalized_url=url,
                        local_ref=None,
                    )
                )
        return candidates

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if candidate.local_ref is not None:
            return ResolveResult(
                request=ResolvedMediaRequest(
                    url=candidate.url,
                    downloader=self,
                    provider_item_ref=candidate.local_ref,
                    normalized_url=candidate.normalized_url,
                )
            )
        if candidate.link_type != "share":
            return ResolveResult(request=None, failure_reason="unsupported")

        ydl_opts = build_metadata_ytdlp_options(cookie_filepath=self.cookie_filepath)
        try:
            with (
                yt_dlp.YoutubeDL(ydl_opts) as ydl,
                ydl.urlopen(Request(candidate.url)) as response,
            ):
                redirected_url = response.url
        except Exception as error:
            raise ResolutionError(
                candidate.normalized_url or candidate.url,
                _classify_reddit_error(error),
            ) from error

        post_id = _post_id_from_url(redirected_url)
        if post_id is None:
            raise ResolutionError(
                candidate.normalized_url or candidate.url, "unsupported"
            )
        normalized_url = _canonical_post_url(post_id)
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=candidate.url,
                downloader=self,
                provider_item_ref=ProviderItemRef(
                    self.provider, self.media_kind, post_id
                ),
                normalized_url=normalized_url,
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        url = request.normalized_url or request.url
        ref = request.provider_item_ref
        try:
            post_data = self._load_post_data(url, ref.provider_item_id)
            if _is_hosted_video(post_data):
                assets = [self._download_video(url, ref, context)]
            else:
                image_candidates = _image_candidates(post_data)
                if image_candidates:
                    assets = self._download_images(image_candidates, ref, context)
                elif post_data.get("is_self") is True:
                    assets = []
                else:
                    return MediaDownloadResult(media=None, failure_reason="unsupported")

            now = datetime.now()
            return MediaDownloadResult(
                media=MediaItem(
                    id=ref.media_id,
                    provider=ref.provider,
                    media_kind=ref.media_kind,
                    provider_item_id=ref.provider_item_id,
                    original_url=url,
                    title=str(post_data.get("title") or ""),
                    description=_optional_string(post_data.get("selftext")),
                    metadata={
                        "like_count": int(post_data.get("ups") or 0),
                        "comment_count": int(post_data.get("num_comments") or 0),
                        "over_18": bool(post_data.get("over_18")),
                        **({"text_only": True} if not assets else {}),
                    },
                    assets=assets,
                    created_at=now,
                    updated_at=now,
                )
            )
        except UnsupportedRedditMediaError as error:
            logger.warning("Unsupported Reddit media at %s (%s)", url, error)
            return MediaDownloadResult(media=None, failure_reason="unsupported")
        except Exception as error:
            failure_reason = _classify_reddit_error(error)
            if failure_reason in ("auth", "blocked"):
                logger.warning(
                    "Failed to download Reddit post from %s: %s (%s)",
                    url,
                    failure_reason,
                    error,
                )
            else:
                logger.exception(
                    "Failed to download Reddit post from %s (%s)", url, error
                )
            return MediaDownloadResult(media=None, failure_reason=failure_reason)

    def _load_post_data(self, url: str, post_id: str) -> dict[str, Any]:
        ydl_opts = build_metadata_ytdlp_options(cookie_filepath=self.cookie_filepath)
        with ExitStack() as stack:
            ydl = stack.enter_context(yt_dlp.YoutubeDL(ydl_opts))
            # Reddit's anonymous JSON API requires session cookies obtained from an
            # initial page request. Account cookies, when supplied, remain in use.
            stack.enter_context(ydl.urlopen(Request("https://old.reddit.com/")))
            response = stack.enter_context(
                ydl.urlopen(Request(f"{url.rstrip('/')}/.json"))
            )
            payload = json.load(response)

        if isinstance(payload, Mapping) and payload.get("error") == 403:
            reason = payload.get("reason")
            if reason == "quarantined":
                raise RedditAuthenticationRequiredError(
                    "Quarantined subreddit; an account that has opted in is required"
                )
            if reason == "private":
                raise RedditAuthenticationRequiredError(
                    "Private subreddit; an account that has been approved is required"
                )
            raise RedditMetadataError(
                f"Reddit rejected metadata access: {reason or '403'}"
            )

        try:
            post_data = payload[0]["data"]["children"][0]["data"]
        except (IndexError, KeyError, TypeError) as error:
            raise RedditMetadataError(
                f"Reddit returned no post metadata for {post_id}"
            ) from error
        if not isinstance(post_data, dict) or str(post_data.get("id")) != post_id:
            raise RedditMetadataError(
                f"Reddit returned mismatched post metadata for {post_id}"
            )
        return cast("dict[str, Any]", post_data)

    def _download_video(
        self,
        url: str,
        ref: ProviderItemRef,
        context: DownloadContext,
    ) -> MediaAsset:
        ydl_opts = build_download_ytdlp_options(
            output_dir=context.output_dir,
            cookie_filepath=self.cookie_filepath,
            provider=ref.provider,
            media_kind=ref.media_kind,
            provider_item_id=ref.provider_item_id,
        )
        # Reddit normally exposes video and audio as separate streams.
        ydl_opts["format"] = "bestvideo+bestaudio/best"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info: _InfoDict = ydl.extract_info(url, download=False)
            filepath = ydl.prepare_filename(info)
            ydl.download([url])
        return map_video_asset(info, filepath=filepath)

    def _download_images(
        self,
        candidates: list[ImageCandidate],
        ref: ProviderItemRef,
        context: DownloadContext,
    ) -> list[MediaAsset]:
        scoped_dir = (
            context.output_dir / ref.provider / ref.media_kind / ref.provider_item_id
        )
        scoped_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts = build_metadata_ytdlp_options(cookie_filepath=self.cookie_filepath)
        assets: list[MediaAsset] = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for index, candidate in enumerate(candidates):
                image_url = candidate["url"]
                _validate_image_url(image_url)
                extension = _image_extension(image_url)
                filepath = scoped_dir / f"{ref.provider_item_id}-{index}.{extension}"
                response = ydl.urlopen(Request(image_url, headers={"Accept": "*/*"}))
                try:
                    _validate_image_url(response.url)
                    content_type = response.headers.get("Content-Type", "")
                    if content_type and not content_type.lower().startswith("image/"):
                        raise UnsupportedRedditMediaError(
                            f"expected an image response, got {content_type}"
                        )
                    _write_image(response, filepath)
                finally:
                    response.close()
                image_info = {**candidate, "filesize": filepath.stat().st_size}
                assets.append(
                    map_image_asset(
                        image_info, filepath=str(filepath), asset_index=index
                    )
                )
        return assets


def _classify_reddit_error(error: Exception) -> DownloadFailureReason:
    if isinstance(error, RedditAuthenticationRequiredError):
        return "auth"
    if isinstance(error, DownloadError):
        message = str(error).lower()
        if any(marker in message for marker in REDDIT_AUTH_MARKERS):
            return "auth"
        if any(marker in message for marker in REDDIT_BLOCK_MARKERS):
            return "blocked"
    return "unknown"


def _post_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _is_allowed_https_url(parsed, REDDIT_HOSTS):
        return None
    match = CANONICAL_PATH_PATTERN.match(parsed.path)
    return match.group("id") if match is not None else None


def _canonical_post_url(post_id: str) -> str:
    return f"https://www.reddit.com/comments/{post_id}/"


def _is_hosted_video(post_data: Mapping[str, Any]) -> bool:
    if post_data.get("post_hint") == "hosted:video":
        return True
    secure_media = post_data.get("secure_media")
    return isinstance(secure_media, Mapping) and isinstance(
        secure_media.get("reddit_video"), Mapping
    )


def _image_candidates(post_data: Mapping[str, Any]) -> list[ImageCandidate]:
    gallery_data = post_data.get("gallery_data")
    if isinstance(gallery_data, Mapping):
        items = gallery_data.get("items")
        media_metadata = post_data.get("media_metadata")
        if (
            not isinstance(items, list)
            or not items
            or not isinstance(media_metadata, Mapping)
        ):
            return []

        gallery_candidates: list[ImageCandidate] = []
        for item in items:
            if not isinstance(item, Mapping):
                return []
            candidate = _metadata_image_candidate(
                media_metadata.get(item.get("media_id"))
            )
            if candidate is None:
                return []
            gallery_candidates.append(candidate)
        return gallery_candidates

    preview_candidate = _preview_source(post_data)
    direct_url = post_data.get("url")
    if post_data.get("post_hint") == "image" and isinstance(direct_url, str):
        if preview_candidate is None:
            preview_candidate = {"url": html.unescape(direct_url)}
        else:
            preview_candidate["url"] = html.unescape(direct_url)
        return [preview_candidate]

    # Link posts can still have a Reddit-hosted full-size preview. Returning that
    # preview is useful without downloading from the external site.
    return [preview_candidate] if preview_candidate is not None else []


def _metadata_image_candidate(metadata: object) -> ImageCandidate | None:
    if not isinstance(metadata, Mapping) or metadata.get("e") != "Image":
        return None
    source = metadata.get("s")
    if not isinstance(source, Mapping) or not isinstance(source.get("u"), str):
        return None
    return {
        "url": html.unescape(source["u"]),
        "width": source.get("x"),
        "height": source.get("y"),
    }


def _preview_source(post_data: Mapping[str, Any]) -> ImageCandidate | None:
    preview = post_data.get("preview")
    if not isinstance(preview, Mapping):
        return None
    images = preview.get("images")
    if not isinstance(images, list) or not images:
        return None
    first_image = images[0]
    if not isinstance(first_image, Mapping):
        return None
    source = first_image.get("source")
    if not isinstance(source, Mapping) or not isinstance(source.get("url"), str):
        return None
    return {
        "url": html.unescape(source["url"]),
        "width": source.get("width"),
        "height": source.get("height"),
    }


def _validate_image_url(url: str) -> None:
    if not _is_allowed_https_url(urlparse(url), REDDIT_IMAGE_HOSTS):
        raise UnsupportedRedditMediaError("image URL is not on an allowed Reddit CDN")


def _is_allowed_https_url(parsed: ParseResult, allowed_hosts: set[str]) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() in allowed_hosts
        and port is None
        and parsed.username is None
        and parsed.password is None
    )


def _write_image(response: Any, filepath: Path) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_REDDIT_IMAGE_BYTES:
                raise UnsupportedRedditMediaError("image exceeds the size limit")
        except ValueError:
            pass

    temporary_path = filepath.with_name(f"{filepath.name}.part")
    bytes_written = 0
    try:
        with temporary_path.open("wb") as output_file:
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                bytes_written += len(chunk)
                if bytes_written > MAX_REDDIT_IMAGE_BYTES:
                    raise UnsupportedRedditMediaError("image exceeds the size limit")
                output_file.write(chunk)
        temporary_path.replace(filepath)
    finally:
        temporary_path.unlink(missing_ok=True)


def _image_extension(url: str) -> str:
    extension = Path(urlparse(url).path).suffix.lower().lstrip(".")
    return extension if extension in IMAGE_EXTENSIONS else "jpg"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
