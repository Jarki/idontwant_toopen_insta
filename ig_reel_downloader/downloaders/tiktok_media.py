from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

import yt_dlp

# The upstream extractor module has no types-yt-dlp stub.
from yt_dlp.extractor.tiktok import TikTokIE  # type: ignore[import-not-found]
from yt_dlp.networking import Request
from yt_dlp.utils import UnsupportedError

from ig_reel_downloader.downloaders.base import DownloadContext, ProviderItemRef
from ig_reel_downloader.downloaders.tiktok_urls import (
    CANONICAL_POST_URL_PATTERN,
    normalize_tiktok_post_url,
)
from ig_reel_downloader.downloaders.yt_dlp_support import (
    map_image_asset,
    map_video_asset,
)
from ig_reel_downloader.repository.models import MediaAsset

if TYPE_CHECKING:
    from yt_dlp.extractor.common import _InfoDict

MAX_TIKTOK_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024


class TikTokMediaStrategy(Protocol):
    def load_info(self, ydl: yt_dlp.YoutubeDL, url: str) -> Mapping[str, Any]: ...

    def download_assets(
        self,
        ydl: yt_dlp.YoutubeDL,
        info: Mapping[str, Any],
        url: str,
        ref: ProviderItemRef,
        context: DownloadContext,
    ) -> list[MediaAsset]: ...


class TikTokVideoStrategy:
    def load_info(self, ydl: yt_dlp.YoutubeDL, url: str) -> Mapping[str, Any]:
        info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            raise UnsupportedError("TikTok video returned invalid metadata")
        return info

    def download_assets(
        self,
        ydl: yt_dlp.YoutubeDL,
        info: Mapping[str, Any],
        url: str,
        ref: ProviderItemRef,
        context: DownloadContext,
    ) -> list[MediaAsset]:
        video_info = cast("_InfoDict", info)
        filepath = ydl.prepare_filename(video_info)
        ydl.download([url])
        return [map_video_asset(info, filepath=filepath)]


class TikTokPhotoStrategy:
    def load_info(self, ydl: yt_dlp.YoutubeDL, url: str) -> Mapping[str, Any]:
        return _extract_photo_info(ydl, url)

    def download_assets(
        self,
        ydl: yt_dlp.YoutubeDL,
        info: Mapping[str, Any],
        url: str,
        ref: ProviderItemRef,
        context: DownloadContext,
    ) -> list[MediaAsset]:
        return _download_photo_assets(ydl, info, ref, context)


DEFAULT_TIKTOK_STRATEGIES: Mapping[str, TikTokMediaStrategy] = {
    "video": TikTokVideoStrategy(),
    "photo": TikTokPhotoStrategy(),
}


def _extract_photo_info(ydl: yt_dlp.YoutubeDL, url: str) -> dict[str, Any]:
    match = CANONICAL_POST_URL_PATTERN.fullmatch(url)
    if match is None or match.group("kind") != "photo":
        raise UnsupportedError(url)
    post_id = match.group("id")
    web_url = normalize_tiktok_post_url(match.group("username"), "video", post_id)
    # The public extractor only returns slideshow audio. Keep the private raw
    # web-data adapter here so upstream changes cannot leak into other providers.
    extractor = cast("Any", TikTokIE(ydl))
    item, status = extractor._extract_web_data_and_status(web_url, post_id)
    if status != 0 or not isinstance(item, dict) or str(item.get("id")) != post_id:
        raise UnsupportedError(f"TikTok photo post unavailable, status code {status}")
    image_post = item.get("imagePost")
    images = image_post.get("images") if isinstance(image_post, Mapping) else None
    if not isinstance(images, list) or not images:
        raise UnsupportedError(url)
    parsed_info = extractor._parse_aweme_video_web(
        item, url, post_id, extract_flat=True
    )
    if not isinstance(parsed_info, dict):
        raise UnsupportedError("TikTok photo post returned invalid metadata")
    parsed_info.update({"webpage_url": url, "images": images})
    return parsed_info


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
    staged_assets: list[tuple[Path, Path, Mapping[str, Any], int, int]] = []
    with TemporaryDirectory(dir=scoped_dir, prefix=".download-") as staging_dir_name:
        staging_dir = Path(staging_dir_name)
        for index, image in enumerate(images):
            if not isinstance(image, Mapping):
                raise UnsupportedError(
                    "TikTok photo metadata contains an invalid image"
                )
            image_url_data = image.get("imageURL")
            if not isinstance(image_url_data, Mapping):
                raise UnsupportedError(
                    "TikTok photo metadata contains an invalid image"
                )
            url_list = image_url_data.get("urlList")
            if (
                not isinstance(url_list, list)
                or not url_list
                or not isinstance(url_list[0], str)
            ):
                raise UnsupportedError(
                    "TikTok photo metadata contains an invalid image"
                )
            image_url = url_list[0]
            _validate_image_url(image_url)
            with cast("Any", ydl).urlopen(Request(image_url)) as response:
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
                filename = f"{ref.provider_item_id}-{index}.{extension}"
                staged_path = staging_dir / filename
                final_path = scoped_dir / filename
                size = 0
                with staged_path.open("wb") as output:
                    while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        if size > MAX_TIKTOK_IMAGE_BYTES:
                            raise UnsupportedError(
                                "TikTok image exceeds the size limit"
                            )
                        output.write(chunk)
                if size == 0:
                    raise UnsupportedError("TikTok returned an empty image")
            staged_assets.append((staged_path, final_path, image, size, index))

        for staged_path, final_path, image, size, index in staged_assets:
            staged_path.replace(final_path)
            assets.append(
                map_image_asset(
                    {
                        "width": image.get("imageWidth"),
                        "height": image.get("imageHeight"),
                        "filesize": size,
                    },
                    filepath=str(final_path),
                    asset_index=index,
                )
            )
    return assets
