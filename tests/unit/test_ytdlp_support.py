from pathlib import Path

from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.yt_dlp_support import (
    build_download_ytdlp_options,
    build_metadata_ytdlp_options,
    classify_download_error,
    map_image_asset,
    map_video_asset,
)


def test_build_ytdlp_options_scopes_output_by_provider_and_item(tmp_path: Path) -> None:
    options = build_download_ytdlp_options(
        output_dir=tmp_path,
        cookie_filepath=None,
        provider="youtube",
        media_kind="video",
        provider_item_id="ABC123",
    )

    assert options["format"] == "best"
    assert options["quiet"] is True
    assert (
        str(tmp_path / "youtube" / "video" / "ABC123" / "%(id)s.%(ext)s")
        == options["outtmpl"]
    )


def test_build_ytdlp_options_adds_existing_cookie_file(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("cookies")

    options = build_download_ytdlp_options(
        output_dir=tmp_path,
        cookie_filepath=cookie_file,
        provider="instagram",
        media_kind="post",
        provider_item_id="ABC123",
    )

    assert options["cookiefile"] == str(cookie_file)


def test_map_video_asset_maps_common_fields() -> None:
    asset = map_video_asset(
        {"width": 1080, "height": 1920, "duration": 9.5, "filesize": 1234},
        filepath="/tmp/video.mp4",
        asset_index=2,
    )

    assert asset.asset_index == 2
    assert asset.asset_type == "video"
    assert asset.filepath == "/tmp/video.mp4"
    assert asset.width == 1080
    assert asset.height == 1920
    assert asset.duration_seconds == 9.5
    assert asset.file_size_bytes == 1234


def test_map_image_asset_maps_common_fields() -> None:
    asset = map_image_asset(
        {"width": 800, "height": 600}, filepath="/tmp/image.jpg", asset_index=1
    )

    assert asset.asset_index == 1
    assert asset.asset_type == "image"
    assert asset.filepath == "/tmp/image.jpg"
    assert asset.width == 800
    assert asset.height == 600


def test_build_metadata_ytdlp_options_has_no_output_template(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("cookies")

    options = build_metadata_ytdlp_options(cookie_filepath=cookie_file)

    assert options["quiet"] is True
    assert options["cookiefile"] == str(cookie_file)
    assert "outtmpl" not in options


def test_classify_download_error_detects_auth() -> None:
    error = DownloadError(
        "Instagram sent an empty media response. Use --cookies for the authentication."
    )

    assert classify_download_error(error) == "auth"
