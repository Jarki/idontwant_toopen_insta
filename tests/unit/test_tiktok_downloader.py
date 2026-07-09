from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader


def test_tiktok_extracts_canonical_video_url() -> None:
    downloader = TikTokDownloader()
    text = "watch https://www.tiktok.com/@alice/video/725123456789?utm_source=x now"

    candidates = downloader.extract_candidates(text)

    assert len(candidates) == 1
    assert candidates[0].provider == "tiktok"
    assert candidates[0].link_type == "video"
    assert (
        candidates[0].normalized_url
        == "https://www.tiktok.com/@alice/video/725123456789"
    )
    assert candidates[0].local_ref == ProviderItemRef(
        "tiktok",
        "video",
        "725123456789",
    )


@pytest.mark.parametrize(
    "url",
    ["https://vm.tiktok.com/ZMabc123/", "https://vt.tiktok.com/ZMabc123/"],
)
def test_tiktok_extracts_share_url_without_local_ref(url: str) -> None:
    downloader = TikTokDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1
    assert candidates[0].provider == "tiktok"
    assert candidates[0].link_type == "share"
    assert candidates[0].normalized_url == url
    assert candidates[0].local_ref is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@alice",
        "https://www.tiktok.com/tag/cats",
        "https://example.com/@alice/video/123",
        "https://www.tiktok.com/@alice/video/",
    ],
)
def test_tiktok_rejects_unsupported_urls(url: str) -> None:
    downloader = TikTokDownloader()

    assert downloader.extract_candidates(url) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@alice/video/725123456789/extra",
        "https://www.tiktok.com/@alice/video/725123456789abc",
        "https://vm.tiktok.com/ZMabc123/extra",
        "https://vt.tiktok.com/",
    ],
)
def test_tiktok_rejects_prefix_or_incomplete_urls(url: str) -> None:
    downloader = TikTokDownloader()

    assert downloader.extract_candidates(url) == []


def test_tiktok_canonical_resolve_uses_local_ref() -> None:
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates(
        "https://www.tiktok.com/@alice/video/725123456789?utm_source=x",
    )[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "tiktok",
        "video",
        "725123456789",
    )
    assert (
        result.request.normalized_url
        == "https://www.tiktok.com/@alice/video/725123456789"
    )
    assert result.request.info is None


def test_tiktok_share_resolve_uses_metadata_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {"quiet": True}

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://vm.tiktok.com/ZMabc123/"
            assert download is False
            return {
                "id": "725123456789",
                "webpage_url": "https://www.tiktok.com/@alice/video/725123456789",
            }

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates("https://vm.tiktok.com/ZMabc123/")[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "tiktok",
        "video",
        "725123456789",
    )
    assert (
        result.request.normalized_url
        == "https://www.tiktok.com/@alice/video/725123456789"
    )
    assert result.request.info is not None


def test_tiktok_share_resolve_raises_resolution_error(
    monkeypatch: pytest.MonkeyPatch,
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
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates("https://vm.tiktok.com/ZMabc123/")[0]

    with pytest.raises(ResolutionError) as exc_info:
        downloader.resolve(candidate)

    assert exc_info.value.url == "https://vm.tiktok.com/ZMabc123/"
    assert exc_info.value.failure_reason == "auth"


def test_tiktok_download_maps_single_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {
                "outtmpl": str(
                    tmp_path / "tiktok" / "video" / "725123456789" / "%(id)s.%(ext)s"
                ),
                "format": "best",
                "quiet": True,
            }

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://www.tiktok.com/@alice/video/725123456789"
            assert download is False
            return {
                "id": "725123456789",
                "title": "TikTok",
                "description": "desc",
                "ext": "mp4",
                "duration": 10,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "725123456789.mp4")

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://www.tiktok.com/@alice/video/725123456789"]

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
    assert result.media.assets[0].asset_type == "video"


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

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://www.tiktok.com/@alice/video/725123456789"]

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
        info={"id": "725123456789", "title": "TikTok", "ext": "mp4"},
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is not None
    assert result.media.id == "tiktok:video:725123456789"
