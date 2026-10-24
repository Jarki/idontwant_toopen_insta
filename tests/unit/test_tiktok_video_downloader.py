from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader, _tiktok_metadata


def test_tiktok_download_maps_single_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, _options: dict[str, object]) -> None:
            self.downloaded = False

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, _url: str, download: bool = False) -> dict[str, object]:
            assert download is False
            return {
                "id": "725123456789",
                "title": "TikTok",
                "description": "desc",
                "view_count": 1_000,
                "like_count": 0,
                "comment_count": 7,
                "repost_count": 8,
                "save_count": 9,
                "uploader": "alice",
                "channel": "Alice",
                "track": "Example Song",
                "artists": ["First Artist", "Second Artist"],
                "album": "Example Album",
                "timestamp": 1_700_000_000,
                "ext": "mp4",
                "duration": 10,
                "webpage_url": "https://www.tiktok.com/@alice/video/725123456789",
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "725123456789.mp4")

        def download(self, _urls: list[str]) -> None:
            self.downloaded = True

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    request = ResolvedMediaRequest(
        url="https://www.tiktok.com/@alice/video/725123456789",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("tiktok", "video", "725123456789"),
        normalized_url="https://www.tiktok.com/@alice/video/725123456789",
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.id == "tiktok:video:725123456789"
    assert result.media.provider == "tiktok"
    assert result.media.media_kind == "video"
    assert result.media.provider_item_id == "725123456789"
    assert result.media.title == "TikTok"
    assert result.media.description == "desc"
    assert result.media.metadata == {
        "view_count": 1_000,
        "like_count": 0,
        "comment_count": 7,
        "repost_count": 8,
        "save_count": 9,
        "uploader": "alice",
        "channel": "Alice",
        "track": "Example Song",
        "artists": ["First Artist", "Second Artist"],
        "album": "Example Album",
        "timestamp": 1_700_000_000,
    }
    assert result.media.assets[0].asset_type == "video"


def test_tiktok_metadata_omits_invalid_artist_lists() -> None:
    assert _tiktok_metadata({"artists": ["artist", 3]}) == {}
    assert _tiktok_metadata({"artists": "artist"}) == {}


def test_tiktok_download_normalizes_bot_detection_failure(
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
                "[TikTok] 7668090902816017671: Unexpected response from webpage request"
            )

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    request = ResolvedMediaRequest(
        url="https://www.tiktok.com/@alice/video/7668090902816017671",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("tiktok", "video", "7668090902816017671"),
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is None
    assert result.failure_reason == "blocked"


def test_tiktok_download_reuses_request_info(
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
            raise AssertionError("metadata should be reused from the resolved request")

        def prepare_filename(self, info: dict[str, object]) -> str:
            assert info["id"] == "725123456789"
            return str(tmp_path / "725123456789.mp4")

        def download(self, _urls: list[str]) -> None:
            pass

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    request = ResolvedMediaRequest(
        url="https://vm.tiktok.com/ZMabc123/",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("tiktok", "video", "725123456789"),
        normalized_url="https://www.tiktok.com/@alice/video/725123456789",
        info={
            "id": "725123456789",
            "title": "TikTok",
            "ext": "mp4",
        },
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is not None
    assert result.media.id == "tiktok:video:725123456789"
