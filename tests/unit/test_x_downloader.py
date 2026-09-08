from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.x import XDownloader


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://x.com/alice/status/1891234567890123456",
            "https://x.com/alice/status/1891234567890123456",
        ),
        (
            "https://www.x.com/alice/status/1891234567890123456?s=20&t=tracking",
            "https://x.com/alice/status/1891234567890123456",
        ),
        (
            "https://twitter.com/alice/status/1891234567890123456/video/1",
            "https://x.com/alice/status/1891234567890123456",
        ),
        (
            "https://x.com/i/status/1891234567890123456",
            "https://x.com/i/status/1891234567890123456",
        ),
    ],
)
def test_x_extracts_post_urls(url: str, expected: str) -> None:
    downloader = XDownloader()

    candidates = downloader.extract_candidates(f"watch {url} now")

    assert len(candidates) == 1
    assert candidates[0].url == url
    assert candidates[0].provider == "x"
    assert candidates[0].link_type == "post"
    assert candidates[0].normalized_url == expected
    assert candidates[0].local_ref == ProviderItemRef(
        "x", "post", "1891234567890123456"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/alice",
        "https://x.com/alice/status/",
        "https://x.com/alice/status/not-a-number",
        "https://x.com/alice/status/123/extra",
        "https://example.com/alice/status/123",
    ],
)
def test_x_rejects_unsupported_urls(url: str) -> None:
    assert XDownloader().extract_candidates(url) == []


def test_x_strips_trailing_punctuation() -> None:
    downloader = XDownloader()

    candidates = downloader.extract_candidates("(https://x.com/alice/status/123).")

    assert len(candidates) == 1
    assert candidates[0].url == "https://x.com/alice/status/123"


def test_x_resolve_uses_local_post_identity() -> None:
    downloader = XDownloader()
    candidate = downloader.extract_candidates(
        "https://www.x.com/alice/status/1891234567890123456?s=20"
    )[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "x", "post", "1891234567890123456"
    )
    assert (
        result.request.normalized_url
        == "https://x.com/alice/status/1891234567890123456"
    )


def test_x_download_maps_single_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {
                "outtmpl": str(
                    tmp_path / "x" / "post" / "1891234567890123456" / "%(id)s.%(ext)s"
                ),
                "format": "best",
                "quiet": True,
            }

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://x.com/alice/status/1891234567890123456"
            assert download is False
            return {
                "id": "1891234567890123456",
                "title": "Alice - post",
                "description": "A video post",
                "like_count": 42,
                "ext": "mp4",
                "duration": 12,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / f"{info['id']}.mp4")

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://x.com/alice/status/1891234567890123456"]

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = XDownloader()
    request = ResolvedMediaRequest(
        url="https://x.com/alice/status/1891234567890123456",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("x", "post", "1891234567890123456"),
        normalized_url="https://x.com/alice/status/1891234567890123456",
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.id == "x:post:1891234567890123456"
    assert result.media.provider == "x"
    assert result.media.media_kind == "post"
    assert result.media.title == "Alice - post"
    assert result.media.description == "A video post"
    assert result.media.metadata["like_count"] == 42
    assert len(result.media.assets) == 1
    assert result.media.assets[0].asset_type == "video"


def test_x_download_maps_multiple_videos(
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
            return {
                "id": "123",
                "title": "Two videos",
                "entries": [
                    {"id": "123-1", "ext": "mp4", "duration": 3},
                    {"id": "123-2", "ext": "mp4", "duration": 4},
                ],
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / f"{info['id']}.mp4")

        def download(self, urls: list[str]) -> None:
            pass

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = XDownloader()
    request = ResolvedMediaRequest(
        url="https://x.com/alice/status/123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("x", "post", "123"),
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is not None
    assert [asset.asset_index for asset in result.media.assets] == [0, 1]
    assert [asset.duration_seconds for asset in result.media.assets] == [3, 4]


def test_x_download_falls_back_to_post_images(
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
            raise DownloadError("No video could be found in this tweet")

    page = """
        <meta property="og:title" content="Alice (@alice) on X">
        <meta property="og:description" content="Photo post">
        <meta property="og:image"
              content="https://pbs.twimg.com/media/first.jpg:large">
        <meta property="og:image:secure_url"
              content="https://pbs.twimg.com/media/first.jpg:large">
        <meta property="og:image"
              content="https://pbs.twimg.com/media/second.png:large">
    """

    def fake_download_image(url: str, filepath: Path) -> None:
        filepath.write_bytes(url.encode())

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x._fetch_x_page", lambda _: page
    )
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x._download_image_file",
        fake_download_image,
    )
    downloader = XDownloader()
    request = ResolvedMediaRequest(
        url="https://x.com/alice/status/123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("x", "post", "123"),
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.title == "Alice (@alice) on X"
    assert result.media.description == "Photo post"
    assert [asset.asset_type for asset in result.media.assets] == ["image", "image"]
    assert [Path(asset.filepath).suffix for asset in result.media.assets] == [
        ".jpg",
        ".png",
    ]
    assert all(Path(asset.filepath).is_file() for asset in result.media.assets)


def test_x_download_maps_text_only_post(
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
            raise DownloadError("No video could be found in this tweet")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x._fetch_x_page",
        lambda _: (
            '<meta property="og:title" content="Alice (@alice) on X">'
            '<meta property="og:description" content="Text-only post body">'
        ),
    )
    downloader = XDownloader()
    request = ResolvedMediaRequest(
        url="https://x.com/alice/status/123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("x", "post", "123"),
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.title == "Alice (@alice) on X"
    assert result.media.description == "Text-only post body"
    assert result.media.assets == []
    assert result.media.metadata["text_only"] is True
    assert result.media.metadata["body_only"] is True


def test_x_download_normalizes_failure(
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
            raise DownloadError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = XDownloader()
    request = ResolvedMediaRequest(
        url="https://x.com/alice/status/123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("x", "post", "123"),
    )

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is None
    assert result.failure_reason == "blocked"
