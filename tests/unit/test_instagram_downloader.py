from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import DownloadContext
from ig_reel_downloader.downloaders.instagram import InstagramReelDownloader


def test_extract_urls_returns_match_spans() -> None:
    downloader = InstagramReelDownloader()
    text = "before https://www.instagram.com/reel/ABC-123 after"

    matches = downloader.extract_urls(text)

    assert len(matches) == 1
    assert matches[0].url == "https://www.instagram.com/reel/ABC-123"
    assert matches[0].start == text.index("https://")
    assert matches[0].end == matches[0].start + len(matches[0].url)
    assert matches[0].downloader is downloader


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/reel/DFvr8JTuscr", "DFvr8JTuscr"),
        ("https://www.instagram.com/reel/DGcc6bHIKFY", "DGcc6bHIKFY"),
        ("https://www.instagram.com/reel/DJcm-GGRTJq/", "DJcm-GGRTJq"),
        ("https://www.instagram.com/reel/D?", "D"),
    ],
)
def test_get_provider_item_ref_extracts_current_reel_ids(
    url: str,
    expected: str,
) -> None:
    downloader = InstagramReelDownloader()

    ref = downloader.get_provider_item_ref(url)

    assert ref is not None
    assert ref.provider == "instagram"
    assert ref.media_kind == "reel"
    assert ref.provider_item_id == expected
    assert ref.media_id == f"instagram:reel:{expected}"


def test_get_provider_item_ref_rejects_non_reel_url() -> None:
    downloader = InstagramReelDownloader()

    assert downloader.get_provider_item_ref("https://www.instagram.com/p/ABC") is None


def test_download_maps_ytdlp_info_to_media_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[object] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            calls.append(options)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://www.instagram.com/reel/ABC123"
            assert download is False
            return {
                "id": "ABC123",
                "title": "Title",
                "description": "Description",
                "like_count": 12,
                "comments": [{"text": "nice"}],
                "duration": 9.5,
                "filesize": 1234,
                "width": 1080,
                "height": 1920,
                "ext": "mp4",
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "ABC123.mp4")

        def download(self, urls: list[str]) -> None:
            calls.append(urls)

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.instagram.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = InstagramReelDownloader(cookie_filepath=None)

    result = downloader.download(
        "https://www.instagram.com/reel/ABC123",
        DownloadContext(output_dir=tmp_path),
    )

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.id == "instagram:reel:ABC123"
    assert result.media.provider == "instagram"
    assert result.media.media_kind == "reel"
    assert result.media.provider_item_id == "ABC123"
    assert result.media.metadata["like_count"] == 12
    assert result.media.metadata["comments"] == [{"text": "nice"}]
    assert result.media.assets[0].asset_type == "video"
    assert result.media.assets[0].filepath == str(tmp_path / "ABC123.mp4")


def test_download_returns_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            raise DownloadError(
                "Instagram sent an empty media response. "
                "Use --cookies for the authentication."
            )

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.instagram.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = InstagramReelDownloader()

    result = downloader.download(
        "https://www.instagram.com/reel/ABC123",
        DownloadContext(output_dir=tmp_path),
    )

    assert result.media is None
    assert result.failure_reason == "auth"
