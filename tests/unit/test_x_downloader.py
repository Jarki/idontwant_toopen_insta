from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.x import (
    MAX_X_IMAGE_BYTES,
    MAX_X_VIDEO_BYTES,
    UnsupportedXMediaError,
    XDownloader,
    _download_image_file,
    _is_allowed_x_image_url,
)


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
            options_without_hooks = dict(options)
            progress_hooks = options_without_hooks.pop("progress_hooks")
            assert isinstance(progress_hooks, list)
            assert len(progress_hooks) == 1
            assert callable(progress_hooks[0])
            assert options_without_hooks == {
                "outtmpl": str(
                    tmp_path / "x" / "post" / "1891234567890123456" / "%(id)s.%(ext)s"
                ),
                "format": "best",
                "quiet": True,
                "max_filesize": MAX_X_VIDEO_BYTES,
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
                "filesize": 1_000_000,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / f"{info['id']}.mp4")

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://x.com/alice/status/1891234567890123456"]
            (tmp_path / "1891234567890123456.mp4").write_bytes(b"video")

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
                    {
                        "id": "123-1",
                        "ext": "mp4",
                        "duration": 3,
                        "filesize": 1_000_000,
                    },
                    {
                        "id": "123-2",
                        "ext": "mp4",
                        "duration": 4,
                        "filesize": 2_000_000,
                    },
                ],
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / f"{info['id']}.mp4")

        def download(self, urls: list[str]) -> None:
            (tmp_path / "123-1.mp4").write_bytes(b"first")
            (tmp_path / "123-2.mp4").write_bytes(b"second")

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


def test_x_rejects_video_with_known_size_over_100_mb(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options["max_filesize"] == MAX_X_VIDEO_BYTES

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            return {
                "id": "123",
                "title": "Too large",
                "ext": "mp4",
                "filesize": MAX_X_VIDEO_BYTES + 1,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            raise AssertionError("oversized video must be rejected before preparation")

        def download(self, urls: list[str]) -> None:
            raise AssertionError("oversized video must not be downloaded")

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
    assert result.failure_reason == "unsupported"


def test_x_rejects_unknown_video_size_before_download(
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
            return {"id": "123", "title": "Unknown size", "ext": "mp4"}

        def prepare_filename(self, info: dict[str, object]) -> str:
            raise AssertionError(
                "unknown-size video must be rejected before preparation"
            )

        def download(self, urls: list[str]) -> None:
            raise AssertionError("unknown-size video must not be downloaded")

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
    assert result.failure_reason == "unsupported"


def test_x_rejects_silently_skipped_video_download(
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
                "title": "Skipped",
                "ext": "mp4",
                "filesize": 1_000_000,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "x" / "post" / "123" / "123.mp4")

        def download(self, urls: list[str]) -> None:
            return None

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
    assert result.failure_reason == "unsupported"


def test_x_stops_video_crossing_100_mb_and_removes_partial_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            progress_hooks = options["progress_hooks"]
            assert isinstance(progress_hooks, list)
            self.progress_hook = progress_hooks[0]

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            return {
                "id": "123",
                "title": "Unexpected growth",
                "ext": "mp4",
                "filesize_approx": 99_000_000,
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "x" / "post" / "123" / "123.mp4")

        def download(self, urls: list[str]) -> None:
            partial = tmp_path / "x" / "post" / "123" / "123.mp4.part"
            partial.write_bytes(b"partial")
            self.progress_hook({"downloaded_bytes": MAX_X_VIDEO_BYTES + 1})

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
    assert result.failure_reason == "unsupported"
    assert not (tmp_path / "x" / "post" / "123" / "123.mp4.part").exists()


def test_x_removes_completed_outputs_when_multi_video_post_exceeds_limit(
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
                    {"id": "123-1", "ext": "mp4", "filesize": 1_000_000},
                    {"id": "123-2", "ext": "mp4", "filesize": 99_000_000},
                ],
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "x" / "post" / "123" / f"{info['id']}.mp4")

        def download(self, urls: list[str]) -> None:
            scoped_dir = tmp_path / "x" / "post" / "123"
            (scoped_dir / "123-1.mp4").write_bytes(b"first")
            with (scoped_dir / "123-2.mp4").open("wb") as output_file:
                output_file.truncate(MAX_X_VIDEO_BYTES + 1)

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
    assert result.failure_reason == "unsupported"
    assert list((tmp_path / "x" / "post" / "123").iterdir()) == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://pbs.twimg.com/media/image.jpg:large", True),
        ("http://pbs.twimg.com/media/image.jpg:large", False),
        ("https://evil.example/?next=pbs.twimg.com/media/image.jpg", False),
        ("https://pbs.twimg.com.evil.example/media/image.jpg", False),
        ("https://user@pbs.twimg.com/media/image.jpg", False),
    ],
)
def test_x_image_url_allowlist(url: str, expected: bool) -> None:
    assert _is_allowed_x_image_url(url) is expected


def test_x_image_download_rejects_declared_size_over_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeResponse:
        url = "https://pbs.twimg.com/media/image.jpg:large"

        def __init__(self) -> None:
            self.headers = {
                "Content-Type": "image/jpeg",
                "Content-Length": str(MAX_X_IMAGE_BYTES + 1),
            }

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        del request, timeout
        return FakeResponse()

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.x.urllib.request.urlopen",
        fake_urlopen,
    )
    filepath = tmp_path / "image.jpg"

    with pytest.raises(UnsupportedXMediaError, match="size limit"):
        _download_image_file(FakeResponse.url, filepath)

    assert not filepath.exists()
    assert not (tmp_path / "image.jpg.part").exists()


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
        client:VHdlZXQ6MTIz:counts" data favorite_count:42
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
    assert result.media.metadata["like_count"] == 42
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
            'client:VHdlZXQ6MTIz:counts" data favorite_count:73'
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
    assert result.media.metadata["like_count"] == 73
    assert result.media.metadata["text_only"] is True


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
