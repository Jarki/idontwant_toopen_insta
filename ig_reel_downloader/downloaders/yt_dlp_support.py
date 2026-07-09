from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yt_dlp import _Params

from ig_reel_downloader.downloaders.base import DownloadFailureReason
from ig_reel_downloader.repository.models import MediaAsset
from ig_reel_downloader.utils import is_auth_required_download_error


def build_metadata_ytdlp_options(
    *,
    cookie_filepath: Path | None,
) -> _Params:
    options: _Params = {
        "quiet": True,
    }
    if cookie_filepath is not None and cookie_filepath.exists():
        options["cookiefile"] = str(cookie_filepath)
    return options


def build_download_ytdlp_options(
    *,
    output_dir: Path,
    cookie_filepath: Path | None,
    provider: str,
    media_kind: str,
    provider_item_id: str,
) -> _Params:
    scoped_dir = output_dir / provider / media_kind / provider_item_id
    scoped_dir.mkdir(parents=True, exist_ok=True)
    options: _Params = {
        "outtmpl": str(scoped_dir / "%(id)s.%(ext)s"),
        "format": "best",
        "quiet": True,
    }
    if cookie_filepath is not None and cookie_filepath.exists():
        options["cookiefile"] = str(cookie_filepath)
    return options


def map_video_asset(
    info: Mapping[str, Any],
    *,
    filepath: str,
    asset_index: int = 0,
) -> MediaAsset:
    return MediaAsset(
        asset_index=asset_index,
        asset_type="video",
        filepath=filepath,
        width=_optional_int(info.get("width")),
        height=_optional_int(info.get("height")),
        duration_seconds=_optional_float(info.get("duration")),
        file_size_bytes=_optional_int(
            info.get("filesize") or info.get("filesize_approx")
        ),
    )


def map_image_asset(
    info: Mapping[str, Any],
    *,
    filepath: str,
    asset_index: int = 0,
) -> MediaAsset:
    return MediaAsset(
        asset_index=asset_index,
        asset_type="image",
        filepath=filepath,
        width=_optional_int(info.get("width")),
        height=_optional_int(info.get("height")),
        file_size_bytes=_optional_int(
            info.get("filesize") or info.get("filesize_approx")
        ),
    )


def classify_download_error(error: Exception) -> DownloadFailureReason:
    return "auth" if is_auth_required_download_error(error) else "unknown"


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
