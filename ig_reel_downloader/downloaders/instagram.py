from __future__ import annotations

import http.cookiejar
import logging
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yt_dlp
from yt_dlp.utils import DownloadError
from yt_dlp.utils.networking import std_headers

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
from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    classify_download_error,
    map_image_asset,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaItem

if TYPE_CHECKING:
    from yt_dlp import _Params
    from yt_dlp.extractor.common import _InfoDict

logger = logging.getLogger(__name__)
REEL_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.instagram\.com/reel/(?P<id>[a-zA-Z0-9_-]+))"
)
POST_URL_PATTERN = re.compile(
    r"(?P<url>https://www\.instagram\.com/p/(?P<id>[a-zA-Z0-9_-]+)(?:/)?(?:\?[^\s#]*)?(?:#[^\s]*)?)(?=\s|$)"
)
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTENSIONS = {"m4v", "mov", "mp4", "webm"}


def _normalize_instagram_url(kind: str, item_id: str) -> str:
    return f"https://www.instagram.com/{kind}/{item_id}/"


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

        ydl_opts = build_download_ytdlp_options(
            output_dir=context.output_dir,
            cookie_filepath=self.cookie_filepath,
            provider=ref.provider,
            media_kind=ref.media_kind,
            provider_item_id=ref.provider_item_id,
        )

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
                    assets=[map_video_asset(info, filepath=filepath)],
                    created_at=now,
                    updated_at=now,
                )
                return MediaDownloadResult(media=media)
        except Exception as error:
            failure_reason = classify_download_error(error)
            if failure_reason == "auth":
                logger.warning(
                    "Failed to download video from %s: authentication required (%s)",
                    url,
                    error,
                )
            else:
                logger.exception("Failed to download video from %s (%s)", url, error)
            return MediaDownloadResult(media=None, failure_reason=failure_reason)


class InstagramPostDownloader:
    provider = "instagram"
    media_kind = "post"

    def __init__(self, cookie_filepath: Path | None = None) -> None:
        self.cookie_filepath = cookie_filepath

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[UrlCandidate] = []
        for match in POST_URL_PATTERN.finditer(text):
            item_id = match.group("id")
            url = match.group("url").rstrip(".,)")
            ref = ProviderItemRef(self.provider, self.media_kind, item_id)
            candidates.append(
                UrlCandidate(
                    url=url,
                    start=match.start("url"),
                    end=match.start("url") + len(url),
                    downloader=self,
                    provider=self.provider,
                    link_type=self.media_kind,
                    normalized_url=_normalize_instagram_url("p", item_id),
                    local_ref=ref,
                )
            )
        return candidates

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
        download_ydl_opts = build_download_ytdlp_options(
            output_dir=context.output_dir,
            cookie_filepath=self.cookie_filepath,
            provider=ref.provider,
            media_kind=ref.media_kind,
            provider_item_id=ref.provider_item_id,
        )
        extract_ydl_opts = _build_post_extract_ytdlp_options(download_ydl_opts)

        try:
            with yt_dlp.YoutubeDL(extract_ydl_opts) as extract_ydl:
                info: _InfoDict = extract_ydl.extract_info(url, download=False)
                asset_infos = _post_asset_infos(info)

            assets = []
            with yt_dlp.YoutubeDL(download_ydl_opts) as download_ydl:
                for index, asset_info in enumerate(asset_infos):
                    if _is_video_info(asset_info):
                        filepath = _download_video_asset(download_ydl, asset_info)
                        assets.append(
                            map_video_asset(
                                asset_info,
                                filepath=filepath,
                                asset_index=index,
                            )
                        )
                        continue

                    image_info, image_url = _image_download_info(
                        asset_info,
                        asset_index=index,
                        provider_item_id=ref.provider_item_id,
                    )
                    filepath = download_ydl.prepare_filename(
                        cast("_InfoDict", image_info)
                    )
                    _download_image_file(
                        image_url,
                        Path(filepath),
                        cookie_filepath=self.cookie_filepath,
                    )
                    downloaded_path = Path(filepath)
                    if downloaded_path.exists():
                        image_info["filesize"] = downloaded_path.stat().st_size
                    assets.append(
                        map_image_asset(
                            image_info,
                            filepath=filepath,
                            asset_index=index,
                        )
                    )

            if not assets:
                raise DownloadError(f"No downloadable post assets found for {url}")

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
                assets=assets,
                created_at=now,
                updated_at=now,
            )
            return MediaDownloadResult(media=media)
        except Exception as error:
            failure_reason = _classify_post_download_error(error)
            if failure_reason == "auth":
                logger.warning(
                    "Failed to download post from %s: authentication required (%s)",
                    url,
                    error,
                )
            else:
                logger.exception("Failed to download post from %s (%s)", url, error)
            return MediaDownloadResult(media=None, failure_reason=failure_reason)


def _build_post_extract_ytdlp_options(download_ydl_opts: _Params) -> _Params:
    extract_ydl_opts = dict(download_ydl_opts)
    extract_ydl_opts.pop("format", None)
    extract_ydl_opts["ignore_no_formats_error"] = True
    return cast("_Params", extract_ydl_opts)


def _post_asset_infos(info: _InfoDict) -> list[_InfoDict]:
    entries = info.get("entries")
    if not isinstance(entries, list):
        return [info]
    asset_infos = [
        cast("_InfoDict", entry) for entry in entries if isinstance(entry, Mapping)
    ]
    return asset_infos or [info]


def _download_video_asset(ydl: Any, asset_info: Mapping[str, Any]) -> str:
    filepath = ydl.prepare_filename(asset_info)
    ydl.process_info(dict(asset_info))
    return str(filepath)


def _image_download_info(
    info: Mapping[str, Any],
    *,
    asset_index: int,
    provider_item_id: str,
) -> tuple[dict[str, Any], str]:
    image_candidate = _best_image_candidate(info)
    if image_candidate is None:
        raise DownloadError("Instagram post asset has no video formats or image URL")

    image_url = image_candidate["url"]
    image_info = dict(info)
    image_info.update(
        {
            "id": _asset_id(
                info, asset_index=asset_index, provider_item_id=provider_item_id
            ),
            "url": image_url,
            "ext": _safe_image_extension(image_candidate, image_url),
        }
    )
    for key in ("width", "height", "filesize", "filesize_approx"):
        if image_candidate.get(key) is not None:
            image_info[key] = image_candidate[key]
    return image_info, image_url


def _download_image_file(
    url: str,
    filepath: Path,
    *,
    cookie_filepath: Path | None,
) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    opener = _build_image_download_opener(cookie_filepath)
    headers = dict(std_headers)
    headers["Referer"] = "https://www.instagram.com/"
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=60) as response:
        content_type = response.headers.get("Content-Type", "")
        if content_type and not content_type.lower().startswith("image/"):
            raise DownloadError(
                f"Expected image response from Instagram, got {content_type}"
            )
        with filepath.open("wb") as output_file:
            shutil.copyfileobj(response, output_file)


def _build_image_download_opener(
    cookie_filepath: Path | None,
) -> urllib.request.OpenerDirector:
    if cookie_filepath is None or not cookie_filepath.exists():
        return urllib.request.build_opener()

    cookie_jar = http.cookiejar.MozillaCookieJar(str(cookie_filepath))
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def _best_image_candidate(info: Mapping[str, Any]) -> dict[str, Any] | None:
    thumbnail_candidates = info.get("thumbnails")
    if isinstance(thumbnail_candidates, list):
        candidates = [
            dict(candidate)
            for candidate in thumbnail_candidates
            if isinstance(candidate, Mapping) and isinstance(candidate.get("url"), str)
        ]
        if candidates:
            return max(
                enumerate(candidates),
                key=lambda indexed_candidate: _image_candidate_score(
                    indexed_candidate[1],
                    fallback_index=indexed_candidate[0],
                ),
            )[1]

    direct_url = info.get("url")
    if isinstance(direct_url, str) and _is_image_info(info):
        return {"url": direct_url, "ext": info.get("ext")}
    return None


def _image_candidate_score(
    candidate: Mapping[str, Any],
    *,
    fallback_index: int,
) -> tuple[int, int, int, int]:
    width = _optional_int_or_zero(candidate.get("width"))
    height = _optional_int_or_zero(candidate.get("height"))
    preference = _optional_int_or_zero(candidate.get("preference"))
    filesize = _optional_int_or_zero(
        candidate.get("filesize") or candidate.get("filesize_approx")
    )
    return (width * height, preference, filesize, fallback_index)


def _safe_image_extension(candidate: Mapping[str, Any], url: str) -> str:
    candidate_ext = candidate.get("ext")
    if isinstance(candidate_ext, str):
        normalized_ext = candidate_ext.lower().lstrip(".")
        if normalized_ext in IMAGE_EXTENSIONS:
            return normalized_ext

    url_path = urllib.parse.urlparse(url).path
    url_ext = Path(url_path).suffix.lower().lstrip(".")
    if url_ext in IMAGE_EXTENSIONS:
        return url_ext
    return "jpg"


def _asset_id(
    info: Mapping[str, Any],
    *,
    asset_index: int,
    provider_item_id: str,
) -> str:
    info_id = info.get("id")
    if isinstance(info_id, str) and info_id:
        return _safe_filename_stem(info_id)
    return f"{_safe_filename_stem(provider_item_id)}-{asset_index}"


def _safe_filename_stem(value: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
    return stem or "asset"


def _is_video_info(info: Mapping[str, Any]) -> bool:
    formats = info.get("formats")
    if isinstance(formats, list) and any(
        isinstance(format_info, Mapping) and _is_video_format(format_info)
        for format_info in formats
    ):
        return True

    ext = info.get("ext")
    return isinstance(ext, str) and ext.lower() in VIDEO_EXTENSIONS


def _is_video_format(format_info: Mapping[str, Any]) -> bool:
    vcodec = format_info.get("vcodec")
    if isinstance(vcodec, str):
        return vcodec != "none"

    ext = format_info.get("ext")
    if isinstance(ext, str):
        return ext.lower() in VIDEO_EXTENSIONS
    return format_info.get("width") is not None or format_info.get("height") is not None


def _is_image_info(info: Mapping[str, Any]) -> bool:
    ext = info.get("ext")
    return isinstance(ext, str) and ext.lower() in IMAGE_EXTENSIONS


def _optional_int_or_zero(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _classify_post_download_error(error: Exception) -> DownloadFailureReason:
    if isinstance(error, urllib.error.HTTPError) and error.code in {401, 403}:
        return "auth"
    return classify_download_error(error)
