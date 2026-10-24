import io
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
)
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader


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
        "ig_reel_downloader.downloaders.tiktok_media.TikTokIE._extract_web_data_and_status",
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
        "ig_reel_downloader.downloaders.tiktok_media.TikTokIE._extract_web_data_and_status",
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
            "ig_reel_downloader.downloaders.tiktok_media.MAX_TIKTOK_IMAGE_BYTES", 4
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
