import io
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader, _tiktok_metadata


@pytest.mark.parametrize("kind", ["video", "photo"])
def test_tiktok_extracts_canonical_url_with_stable_identity(kind: str) -> None:
    downloader = TikTokDownloader()
    text = f"watch https://www.tiktok.com/@alice/{kind}/725123456789?utm_source=x now"

    candidates = downloader.extract_candidates(text)

    assert len(candidates) == 1
    assert candidates[0].provider == "tiktok"
    assert candidates[0].link_type == kind
    assert candidates[0].normalized_url == (
        f"https://www.tiktok.com/@alice/{kind}/725123456789"
    )
    assert candidates[0].local_ref == ProviderItemRef(
        "tiktok",
        kind,
        "725123456789",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://vm.tiktok.com/ZMabc123/",
        "https://vt.tiktok.com/ZMabc123/",
        "https://vm.tiktok.com/ZMabc123",
        "https://vt.tiktok.com/ZMabc123",
    ],
)
def test_tiktok_extracts_share_url_without_local_ref(url: str) -> None:
    downloader = TikTokDownloader()

    candidates = downloader.extract_candidates(url)

    assert len(candidates) == 1
    assert candidates[0].provider == "tiktok"
    assert candidates[0].link_type == "share"
    assert candidates[0].normalized_url == url.removesuffix("/") + "/"
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


@pytest.mark.parametrize("kind", ["video", "photo"])
def test_tiktok_canonical_resolve_uses_local_ref_without_network(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    def unexpected_youtube_dl(_options: dict[str, object]) -> object:
        raise AssertionError("canonical URLs must resolve without a network request")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        unexpected_youtube_dl,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates(
        f"https://www.tiktok.com/@alice/{kind}/725123456789?utm_source=x",
    )[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "tiktok",
        kind,
        "725123456789",
    )
    assert result.request.normalized_url == (
        f"https://www.tiktok.com/@alice/{kind}/725123456789"
    )
    assert result.request.info is None


class _HeadResponse(AbstractContextManager["_HeadResponse"]):
    def __init__(self, destination: str) -> None:
        self.url = destination

    def __exit__(self, *args: object) -> None:
        return None


@pytest.mark.parametrize("kind", ["video", "photo"])
def test_tiktok_share_resolves_to_canonical_stable_identity(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    destination = f"https://www.tiktok.com/@alice/{kind}/725123456789?_t=tracking"

    class FakeYoutubeDL(AbstractContextManager["FakeYoutubeDL"]):
        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            return None

        def urlopen(self, request: object) -> _HeadResponse:
            assert request.method == "HEAD"
            return _HeadResponse(destination)

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates("https://vt.tiktok.com/ZMabc123/")[0]

    result = downloader.resolve(candidate)

    assert result.request is not None
    assert result.request.provider_item_ref == ProviderItemRef(
        "tiktok", kind, "725123456789"
    )
    assert result.request.normalized_url == destination.split("?")[0]
    assert result.request.info is None


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.com/@alice/photo/725123456789",
        "https://www.tiktok.com/@alice/album/725123456789",
        "https://www.tiktok.com/@alice/photo/not-a-number",
        "/@alice/photo/725123456789",
    ],
)
def test_tiktok_share_rejects_external_or_malformed_destination(
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
) -> None:
    class FakeYoutubeDL(AbstractContextManager["FakeYoutubeDL"]):
        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            return None

        def urlopen(self, _request: object) -> _HeadResponse:
            return _HeadResponse(destination)

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    candidate = TikTokDownloader().extract_candidates(
        "https://vm.tiktok.com/ZMabc123/"
    )[0]

    with pytest.raises(ResolutionError) as exc_info:
        candidate.downloader.resolve(candidate)

    assert exc_info.value.failure_reason == "unsupported"


def test_tiktok_share_resolution_retries_blocking_failures_three_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYoutubeDL(AbstractContextManager["FakeYoutubeDL"]):
        attempts = 0

        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            return None

        def urlopen(self, _request: object) -> _HeadResponse:
            self.__class__.attempts += 1
            raise DownloadError("Unexpected response from webpage request")

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates("https://vt.tiktok.com/ZSq1MVD1B/")[0]

    with pytest.raises(ResolutionError) as exc_info:
        downloader.resolve(candidate)

    assert FakeYoutubeDL.attempts == 3
    assert exc_info.value.failure_reason == "blocked"


def test_tiktok_share_resolution_retries_wrapped_ip_block_three_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYoutubeDL(AbstractContextManager["FakeYoutubeDL"]):
        attempts = 0

        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __exit__(self, *args: object) -> None:
            return None

        def urlopen(self, _request: object) -> _HeadResponse:
            self.__class__.attempts += 1
            try:
                raise ExtractorError(
                    "Your IP address is blocked from accessing this post",
                    expected=True,
                )
            except ExtractorError as error:
                raise DownloadError(str(error), exc_info=sys.exc_info()) from error

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL",
        FakeYoutubeDL,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates("https://vt.tiktok.com/ZSq1MVD1B/")[0]

    with pytest.raises(ResolutionError) as exc_info:
        downloader.resolve(candidate)

    assert FakeYoutubeDL.attempts == 3
    assert exc_info.value.failure_reason == "blocked"


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


@pytest.mark.parametrize("count", [1, 3])
def test_tiktok_photo_download_preserves_all_images_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, count: int
) -> None:
    photo_url = "https://www.tiktok.com/@alice/photo/725123456789"
    images = [
        {
            "imageURL": {"urlList": [f"https://p16.tiktokcdn.com/image-{i}.jpg"]},
            "imageWidth": 640 + i,
            "imageHeight": 480,
        }
        for i in range(count)
    ]
    item = {
        "id": "725123456789",
        "desc": "A photo post",
        "author": {"uniqueId": "alice", "nickname": "Alice"},
        "stats": {"diggCount": 42},
        "imagePost": {"images": images},
    }

    class ImageResponse(io.BytesIO):
        def __init__(self, url: str, content: bytes) -> None:
            super().__init__(content)
            self.url = url
            self.headers = {"Content-Type": "image/jpeg"}

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def urlopen(self, request: object) -> ImageResponse:
            url = request.url
            index = int(url.rsplit("-", 1)[1].split(".")[0])
            return ImageResponse(url, f"image-{index}".encode())

    def extract_web_data(self: object, url: str, post_id: str) -> tuple[dict, int]:
        assert url == photo_url.replace("/photo/", "/video/")
        return item, 0

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.TikTokIE._extract_web_data_and_status",
        extract_web_data,
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates(photo_url + "?is_from_webapp=1")[0]
    request = downloader.resolve(candidate).request
    assert request is not None
    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is not None
    assert result.media.id == "tiktok:photo:725123456789"
    assert result.media.original_url == photo_url
    assert result.media.description == "A photo post"
    assert result.media.metadata["uploader"] == "alice"
    assert result.media.metadata["like_count"] == 42
    assert [
        (a.asset_index, a.asset_type, a.width, a.height, Path(a.filepath).read_bytes())
        for a in result.media.assets
    ] == [(i, "image", 640 + i, 480, f"image-{i}".encode()) for i in range(count)]


@pytest.mark.parametrize(
    "raw_item,status",
    [
        ({}, 10216),
        ({"id": "999", "imagePost": {"images": [{}]}}, 0),
        ({"id": "725123456789", "imagePost": {"images": []}}, 0),
    ],
)
def test_tiktok_photo_missing_or_mismatched_post_is_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_item: dict,
    status: int,
) -> None:
    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.TikTokIE._extract_web_data_and_status",
        lambda _self, _url, _post_id: (raw_item, status),
    )
    downloader = TikTokDownloader()
    candidate = downloader.extract_candidates(
        "https://www.tiktok.com/@alice/photo/725123456789"
    )[0]
    request = downloader.resolve(candidate).request
    assert request is not None

    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is None
    assert result.failure_reason == "unsupported"


@pytest.mark.parametrize(
    "failure", ["html", "external_redirect", "empty", "too_large", "read_error"]
)
def test_tiktok_incomplete_photo_album_is_not_delivered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    class ImageResponse(io.BytesIO):
        def __init__(self, index: int) -> None:
            content = b"pic" if index == 0 else b"photo"
            if failure == "empty" and index == 1:
                content = b""
            super().__init__(content)
            self.url = "https://p16.tiktokcdn.com/a.jpg"
            self.headers = {"Content-Type": "image/jpeg"}
            self.index = index
            if index == 1 and failure == "html":
                self.headers["Content-Type"] = "text/html"
            if index == 1 and failure == "external_redirect":
                self.url = "https://example.com/not-a-tiktok-image"

        def read(self, size: int = -1) -> bytes:
            if self.index == 1 and failure == "read_error":
                raise OSError("connection closed")
            return super().read(size)

    class FakeYoutubeDL:
        def __init__(self, _options: dict[str, object]) -> None:
            self.calls = 0

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def urlopen(self, request: object) -> ImageResponse:
            response = ImageResponse(self.calls)
            self.calls += 1
            return response

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    if failure == "too_large":
        monkeypatch.setattr(
            "ig_reel_downloader.downloaders.tiktok.MAX_TIKTOK_IMAGE_BYTES", 4
        )
    downloader = TikTokDownloader()
    request = ResolvedMediaRequest(
        url="https://www.tiktok.com/@alice/photo/725123456789",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("tiktok", "photo", "725123456789"),
        info={
            "images": [
                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/a.jpg"]}},
                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/b.jpg"]}},
            ]
        },
    )
    result = downloader.download(request, DownloadContext(output_dir=tmp_path))

    assert result.media is None
    assert result.failure_reason == (
        "unknown" if failure == "read_error" else "unsupported"
    )
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_tiktok_failed_overlapping_album_does_not_delete_successful_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failing_reached_second = Event()
    successful_finished = Event()

    class ImageResponse(io.BytesIO):
        def __init__(self, url: str, content: bytes, *, fail: bool = False) -> None:
            super().__init__(content)
            self.url = url
            self.headers = {"Content-Type": "image/jpeg"}
            self.fail = fail

        def read(self, size: int = -1) -> bytes:
            if self.fail:
                raise OSError("connection closed")
            return super().read(size)

    class FakeYoutubeDL:
        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def urlopen(self, request: object) -> ImageResponse:
            index = int(request.url.rsplit("-", 1)[1].split(".")[0])
            if current_thread().name == "failing-download":
                if index == 1:
                    failing_reached_second.set()
                    assert successful_finished.wait(timeout=5)
                    return ImageResponse(request.url, b"", fail=True)
                return ImageResponse(request.url, b"failed-0")
            assert failing_reached_second.wait(timeout=5)
            return ImageResponse(request.url, f"success-{index}".encode())

    monkeypatch.setattr(
        "ig_reel_downloader.downloaders.tiktok.yt_dlp.YoutubeDL", FakeYoutubeDL
    )
    downloader = TikTokDownloader()
    request = ResolvedMediaRequest(
        url="https://www.tiktok.com/@alice/photo/725123456789",
        downloader=downloader,
        provider_item_ref=ProviderItemRef("tiktok", "photo", "725123456789"),
        info={
            "images": [
                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/image-0.jpg"]}},
                {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/image-1.jpg"]}},
            ]
        },
    )
    results: dict[str, MediaDownloadResult] = {}

    def run_failing() -> None:
        results["failing"] = downloader.download(
            request, DownloadContext(output_dir=tmp_path)
        )

    def run_successful() -> None:
        results["successful"] = downloader.download(
            request, DownloadContext(output_dir=tmp_path)
        )
        successful_finished.set()

    failing_thread = Thread(target=run_failing, name="failing-download")
    successful_thread = Thread(target=run_successful, name="successful-download")
    failing_thread.start()
    successful_thread.start()
    failing_thread.join(timeout=5)
    successful_thread.join(timeout=5)

    assert not failing_thread.is_alive()
    assert not successful_thread.is_alive()
    assert results["failing"].media is None
    successful = results["successful"].media
    assert successful is not None
    assert [Path(asset.filepath).read_bytes() for asset in successful.assets] == [
        b"success-0",
        b"success-1",
    ]
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob(".download-*"))
