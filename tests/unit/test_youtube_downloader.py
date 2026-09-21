from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.registry import DownloaderRegistry
from ig_reel_downloader.downloaders.youtube import YouTubeDownloader


def test_youtube_extracts_shorts_url() -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates(
        "https://www.youtube.com/shorts/ABC123?si=tracking"
    )

    assert len(candidates) == 1
    assert candidates[0].link_type == "short"
    assert candidates[0].normalized_url == "https://www.youtube.com/shorts/ABC123"
    assert candidates[0].local_ref == ProviderItemRef("youtube", "short", "ABC123")


def test_youtube_extracts_mobile_shorts_url() -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates("https://m.youtube.com/shorts/ABC123")

    assert len(candidates) == 1
    assert candidates[0].link_type == "short"
    assert candidates[0].normalized_url == "https://www.youtube.com/shorts/ABC123"
    assert candidates[0].local_ref == ProviderItemRef("youtube", "short", "ABC123")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/ABC123?si=tracking", "https://youtu.be/ABC123"),
        (
            "https://www.youtube.com/watch?v=ABC123&feature=share&utm_source=x",
            "https://www.youtube.com/watch?v=ABC123",
        ),
        (
            "https://m.youtube.com/watch?v=ABC123&feature=share",
            "https://www.youtube.com/watch?v=ABC123",
        ),
    ],
)
def test_youtube_extracts_normal_video_candidates(url: str, expected: str) -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1
    assert candidates[0].link_type == "video"
    assert candidates[0].normalized_url == expected
    assert candidates[0].local_ref == ProviderItemRef("youtube", "video", "ABC123")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://m.youtube.com/watch/?v=ABC123",
            "https://www.youtube.com/watch?v=ABC123",
        ),
        (
            "https://www.youtube.com/watch/?v=ABC123&feature=share",
            "https://www.youtube.com/watch?v=ABC123",
        ),
    ],
)
def test_youtube_extracts_watch_url_with_trailing_slash(
    url: str, expected: str
) -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1
    assert candidates[0].link_type == "video"
    assert candidates[0].normalized_url == expected
    assert candidates[0].local_ref == ProviderItemRef("youtube", "video", "ABC123")


@pytest.mark.parametrize(
    "url",
    [
        "https://m.youtube.com/watch?v=ABC123!",
        "https://m.youtube.com/watch?v=ABC123?",
        "https://m.youtube.com/watch?v=ABC123;",
        "https://m.youtube.com/shorts/ABC123!",
        "https://www.youtube.com/watch?v=ABC123!",
        "https://youtu.be/ABC123!",
    ],
)
def test_youtube_extracts_url_with_trailing_punctuation(url: str) -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1
    assert candidates[0].url == url.rstrip(".,;:!?\"')]/")


@pytest.mark.parametrize(
    "url",
    [
        "https://M.youtube.com/watch?v=ABC123",
        "https://m.YOUTUBE.COM/watch?v=ABC123",
        "https://YOUTU.BE/ABC123",
    ],
)
def test_youtube_extracts_url_case_insensitive(url: str) -> None:
    downloader = YouTubeDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1


def test_youtube_mobile_url_passes_through_registry() -> None:
    downloader = YouTubeDownloader()
    registry = DownloaderRegistry([downloader])

    candidates = registry.extract_candidates(
        "Check out https://m.youtube.com/watch?v=ABC123"
    )

    assert len(candidates) == 1
    assert candidates[0].provider == "youtube"
    assert candidates[0].link_type == "video"
    assert candidates[0].normalized_url == "https://www.youtube.com/watch?v=ABC123"
    assert candidates[0].local_ref == ProviderItemRef("youtube", "video", "ABC123")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?feature=share",
        "https://www.youtube.com/@channel",
        "https://www.youtube.com/playlist?list=abc",
        "https://example.com/watch?v=ABC123",
    ],
)
def test_youtube_rejects_unsupported_urls(url: str) -> None:
    downloader = YouTubeDownloader()

    assert downloader.extract_candidates(url) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/shorts/ABC123/extra",
        "https://youtu.be/ABC123/extra",
        "https://www.youtube.com/watch/ABC123",
    ],
)
def test_youtube_rejects_extra_or_wrong_path_segments(url: str) -> None:
    downloader = YouTubeDownloader()

    assert downloader.extract_candidates(url) == []


def test_youtube_resolve_accepts_short_without_metadata() -> None:
    downloader = YouTubeDownloader()
    candidate = downloader.extract_candidates("https://www.youtube.com/shorts/ABC123")[
        0
    ]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "youtube", "short", "ABC123"
    )
    assert result.request.info is None
    assert result.skipped is False


def test_youtube_resolve_accepts_normal_video_under_60(
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
            assert url == "https://youtu.be/ABC123"
            assert download is False
            return {"id": "ABC123", "duration": 59, "title": "short enough"}

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.youtube.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = YouTubeDownloader()
    candidate = downloader.extract_candidates("https://youtu.be/ABC123")[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "youtube", "video", "ABC123"
    )
    assert result.request.info is not None


def test_youtube_resolve_skips_normal_video_over_60(
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
            return {"id": "ABC123", "duration": 61}

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.youtube.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = YouTubeDownloader()
    candidate = downloader.extract_candidates("https://youtu.be/ABC123")[0]

    result = downloader.resolve(candidate)

    assert result.request is None
    assert result.skipped is True
    assert result.failure_reason is None


def test_youtube_resolve_raises_resolution_error(
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
                "Sign in to confirm your age. Use --cookies-from-browser or --cookies."
            )

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.youtube.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = YouTubeDownloader()
    candidate = downloader.extract_candidates("https://youtu.be/ABC123")[0]

    with pytest.raises(ResolutionError) as exc_info:
        downloader.resolve(candidate)

    assert exc_info.value.url == "https://youtu.be/ABC123"
    assert exc_info.value.failure_reason == "auth"


def test_youtube_download_maps_single_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            assert options == {
                "outtmpl": str(
                    tmp_path / "youtube" / "video" / "ABC123" / "%(id)s.%(ext)s"
                ),
                "format": (
                    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a][acodec^=mp4a]"
                    "/best[ext=mp4][vcodec^=avc1][acodec^=mp4a]"
                ),
                "merge_output_format": "mp4",
                "quiet": True,
            }

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            assert url == "https://www.youtube.com/watch?v=ABC123"
            assert download is True
            return {
                "id": "ABC123",
                "title": "YouTube",
                "description": "desc",
                "view_count": 100,
                "like_count": 0,
                "comment_count": 3,
                "channel": "Example Channel",
                "channel_id": "channel-1",
                "channel_url": "https://www.youtube.com/channel/channel-1",
                "upload_date": "20260908",
                "timestamp": 1_700_000_000,
                "ext": "mp4",
                "duration": 59,
                "requested_downloads": [{"filepath": str(tmp_path / "merged.mkv")}],
            }

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(tmp_path / "ABC123.mp4")

        def download(self, urls: list[str]) -> None:
            assert urls == ["https://www.youtube.com/watch?v=ABC123"]

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.youtube.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = YouTubeDownloader()
    request = ResolvedMediaRequest(
        url="https://www.youtube.com/watch?v=ABC123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("youtube", "video", "ABC123"),
        normalized_url="https://www.youtube.com/watch?v=ABC123",
    )

    (tmp_path / "merged.mkv").write_bytes(b"video")
    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.failure_reason is None
    assert result.media is not None
    assert result.media.id == "youtube:video:ABC123"
    assert result.media.provider == "youtube"
    assert result.media.media_kind == "video"
    assert result.media.provider_item_id == "ABC123"
    assert result.media.title == "YouTube"
    assert result.media.description == "desc"
    assert result.media.metadata == {
        "view_count": 100,
        "like_count": 0,
        "comment_count": 3,
        "channel": "Example Channel",
        "channel_id": "channel-1",
        "channel_url": "https://www.youtube.com/channel/channel-1",
        "upload_date": "20260908",
        "timestamp": 1_700_000_000,
        "duration": 59,
    }
    assert len(result.media.assets) == 1
    assert result.media.assets[0].filepath == str(tmp_path / "merged.mkv")
    assert result.media.assets[0].asset_type == "video"


def test_youtube_download_reuses_request_info(
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

        def process_ie_result(
            self, info: dict[str, object], *, download: bool
        ) -> dict[str, object]:
            assert info["id"] == "ABC123"
            assert download is True
            return {
                **info,
                "requested_downloads": [{"filepath": str(tmp_path / "merged.mkv")}],
            }

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.youtube.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = YouTubeDownloader()
    request = ResolvedMediaRequest(
        url="https://www.youtube.com/watch?v=ABC123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("youtube", "video", "ABC123"),
        normalized_url="https://www.youtube.com/watch?v=ABC123",
        info={"id": "ABC123", "title": "YouTube", "ext": "mp4"},
    )

    (tmp_path / "merged.mkv").write_bytes(b"video")
    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is not None
    assert result.media.id == "youtube:video:ABC123"


@pytest.mark.parametrize("compatible", [True, False])
@pytest.mark.parametrize("split_streams", [True, False])
@pytest.mark.parametrize("write_file", [True, False])
def test_youtube_real_format_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    split_streams: bool,
    write_file: bool,
    compatible: bool,
) -> None:
    """Use the real yt-dlp selector, replacing only file/network processing."""
    import yt_dlp

    selected: list[str] = []
    final_path = tmp_path / "final.mkv"

    def process_info(self: object, info: dict[str, object]) -> None:
        selected.append(str(info["format_id"]))
        info["filepath"] = str(final_path)
        if write_file:
            final_path.write_bytes(b"merged video")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "process_info", process_info)
    formats = (
        [
            {
                "format_id": "audio",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "ext": "m4a",
            },
            {
                "format_id": "video",
                "vcodec": "avc1.64001f",
                "acodec": "none",
                "ext": "mp4",
            },
        ]
        if split_streams
        else [
            {
                "format_id": "combined",
                "vcodec": "avc1.64001f",
                "acodec": "mp4a.40.2",
                "ext": "mp4",
            }
        ]
    )
    if not compatible:
        formats = []
    # Higher-quality incompatible streams must not win selection, even in MP4.
    formats.extend(
        [
            {
                "format_id": "vp9",
                "vcodec": "vp9",
                "acodec": "opus",
                "ext": "webm",
                "height": 2160,
            },
            {
                "format_id": "av1",
                "vcodec": "av01.0.12M.08",
                "acodec": "mp4a.40.2",
                "ext": "mp4",
                "height": 2160,
            },
        ]
    )
    downloader = YouTubeDownloader()
    request = ResolvedMediaRequest(
        url="https://www.youtube.com/shorts/ABC123",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("youtube", "short", "ABC123"),
        info={
            "id": "ABC123",
            "title": "Offline regression",
            "extractor": "youtube",
            "formats": [
                {**fmt, "url": f"https://example.invalid/{fmt['format_id']}"}
                for fmt in formats
            ],
        },
    )
    result = downloader.download(request, DownloadContext(output_dir=tmp_path))
    if not compatible:
        assert selected == []
        assert result.media is None
        assert result.failure_reason == "unknown"
        return
    assert selected == ["video+audio" if split_streams else "combined"]
    if write_file:
        assert result.media is not None
        assert result.media.assets[0].filepath == str(final_path)
        assert result.failure_reason is None
    else:
        assert result.media is None
        assert result.failure_reason == "unknown"
