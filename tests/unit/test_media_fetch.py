import datetime
from pathlib import Path

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedUrlMatch,
    UrlMatch,
)
from ig_reel_downloader.media_fetch import MediaFetchService
from ig_reel_downloader.repository.models import MediaAsset, MediaItem


def make_media(filepath: str, *, assets: list[MediaAsset] | None = None) -> MediaItem:
    now = datetime.datetime.now()
    return MediaItem(
        id="instagram:reel:ABC123",
        provider="instagram",
        media_kind="reel",
        provider_item_id="ABC123",
        original_url="https://www.instagram.com/reel/ABC123",
        title="Title",
        description=None,
        metadata={"like_count": 0, "comments": []},
        assets=assets
        if assets is not None
        else [MediaAsset(asset_index=0, asset_type="video", filepath=filepath)],
        created_at=now,
        updated_at=now,
    )


class FakeRepository:
    def __init__(self, cached: MediaItem | None = None) -> None:
        self.cached = cached
        self.inserted: list[MediaItem] = []
        self.lookup: tuple[str, str, str] | None = None

    def create_database(self) -> None:
        raise AssertionError("not used")

    def get_media_by_provider_item(
        self,
        provider: str,
        media_kind: str,
        provider_item_id: str,
    ) -> MediaItem | None:
        self.lookup = (provider, media_kind, provider_item_id)
        return self.cached

    def insert_media(self, media: MediaItem) -> None:
        self.inserted.append(media)


class FakeDownloader:
    provider = "instagram"
    media_kind = "reel"

    def __init__(self, result: MediaDownloadResult) -> None:
        self.result = result
        self.contexts: list[DownloadContext] = []

    def extract_urls(self, text: str) -> list[UrlMatch]:
        return []

    def get_provider_item_ref(self, url: str) -> ProviderItemRef | None:
        return ProviderItemRef("instagram", "reel", "ABC123")

    def download(self, url: str, context: DownloadContext) -> MediaDownloadResult:
        self.contexts.append(context)
        return self.result


def make_match(downloader: FakeDownloader) -> ResolvedUrlMatch:
    return ResolvedUrlMatch(
        url="https://www.instagram.com/reel/ABC123",
        start=0,
        end=39,
        downloader=downloader,
        provider_item_ref=ProviderItemRef("instagram", "reel", "ABC123"),
    )


def test_fetch_reuses_cached_media_when_all_files_exist(tmp_path: Path) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    cached = make_media(str(media_file))
    downloader = FakeDownloader(
        MediaDownloadResult(media=None, failure_reason="unknown")
    )
    repository = FakeRepository(cached=cached)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_match(downloader))

    assert result.media == cached
    assert result.failure_reason is None
    assert downloader.contexts == []
    assert repository.inserted == []


def test_fetch_redownloads_when_cached_asset_file_is_missing(tmp_path: Path) -> None:
    cached = make_media(str(tmp_path / "missing.mp4"))
    downloaded = make_media(str(tmp_path / "downloaded.mp4"))
    downloader = FakeDownloader(MediaDownloadResult(media=downloaded))
    repository = FakeRepository(cached=cached)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_match(downloader))

    assert result.media == downloaded
    assert repository.inserted == [downloaded]
    assert downloader.contexts == [DownloadContext(output_dir=tmp_path)]


def test_fetch_redownloads_zero_asset_cached_item(tmp_path: Path) -> None:
    downloaded = make_media(str(tmp_path / "downloaded.mp4"))
    downloader = FakeDownloader(MediaDownloadResult(media=downloaded))
    repository = FakeRepository(cached=make_media("unused", assets=[]))
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_match(downloader))

    assert result.media == downloaded
    assert repository.inserted == [downloaded]


def test_fetch_returns_download_failure_without_insert(tmp_path: Path) -> None:
    downloader = FakeDownloader(MediaDownloadResult(media=None, failure_reason="auth"))
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_match(downloader))

    assert result.media is None
    assert result.url == "https://www.instagram.com/reel/ABC123"
    assert result.failure_reason == "auth"
    assert repository.inserted == []


def test_fetch_rejects_identity_mismatch_as_unknown_failure(tmp_path: Path) -> None:
    mismatched = make_media(str(tmp_path / "other.mp4"))
    mismatched.provider_item_id = "OTHER"
    mismatched.id = "instagram:reel:OTHER"
    downloader = FakeDownloader(MediaDownloadResult(media=mismatched))
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_match(downloader))

    assert result.media is None
    assert result.failure_reason == "unknown"
    assert repository.inserted == []
