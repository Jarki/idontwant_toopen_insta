import datetime
from pathlib import Path

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolutionError,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
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

    def __init__(
        self,
        result: MediaDownloadResult,
        *,
        skipped: bool = False,
        unresolved: bool = False,
        resolution_error: ResolutionError | None = None,
    ) -> None:
        self.result = result
        self.skipped = skipped
        self.unresolved = unresolved
        self.resolution_error = resolution_error
        self.contexts: list[DownloadContext] = []
        self.download_requests: list[ResolvedMediaRequest] = []

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        return []

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if self.resolution_error is not None:
            raise self.resolution_error
        if self.skipped:
            return ResolveResult(request=None, skipped=True)
        if self.unresolved:
            return ResolveResult(request=None, failure_reason="unsupported")
        ref = ProviderItemRef("instagram", "reel", "ABC123")
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=candidate.url,
                normalized_url=candidate.normalized_url,
                downloader=self,
                provider_item_ref=ref,
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        self.download_requests.append(request)
        self.contexts.append(context)
        return self.result


def make_candidate(
    downloader: FakeDownloader,
    *,
    normalized_url: str | None = None,
) -> UrlCandidate:
    return UrlCandidate(
        url="https://www.instagram.com/reel/ABC123",
        start=0,
        end=39,
        downloader=downloader,
        provider="instagram",
        link_type="reel",
        normalized_url=normalized_url,
        local_ref=ProviderItemRef("instagram", "reel", "ABC123"),
    )


def test_fetch_resolves_before_cache_lookup(tmp_path: Path) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    cached = make_media(str(media_file))
    downloader = FakeDownloader(
        MediaDownloadResult(media=None, failure_reason="unknown")
    )
    repository = FakeRepository(cached=cached)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(
        make_candidate(
            downloader,
            normalized_url="https://www.instagram.com/reel/ABC123",
        )
    )

    assert result.media == cached
    assert repository.lookup == ("instagram", "reel", "ABC123")
    assert downloader.download_requests == []


def test_fetch_returns_skipped_without_cache_lookup_or_download(tmp_path: Path) -> None:
    downloader = FakeDownloader(MediaDownloadResult(media=None), skipped=True)
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.skipped is True
    assert result.media is None
    assert repository.lookup is None
    assert downloader.download_requests == []


def test_fetch_reuses_cached_media_when_all_files_exist(tmp_path: Path) -> None:
    media_file = tmp_path / "ABC123.mp4"
    media_file.write_bytes(b"video")
    cached = make_media(str(media_file))
    downloader = FakeDownloader(
        MediaDownloadResult(media=None, failure_reason="unknown")
    )
    repository = FakeRepository(cached=cached)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

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

    result = service.fetch(make_candidate(downloader))

    assert result.media == downloaded
    assert repository.inserted == [downloaded]
    assert downloader.contexts == [DownloadContext(output_dir=tmp_path)]


def test_fetch_redownloads_zero_asset_cached_item(tmp_path: Path) -> None:
    downloaded = make_media(str(tmp_path / "downloaded.mp4"))
    downloader = FakeDownloader(MediaDownloadResult(media=downloaded))
    repository = FakeRepository(cached=make_media("unused", assets=[]))
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.media == downloaded
    assert repository.inserted == [downloaded]


def test_fetch_returns_download_failure_without_insert(tmp_path: Path) -> None:
    downloader = FakeDownloader(MediaDownloadResult(media=None, failure_reason="auth"))
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.media is None
    assert result.url == "https://www.instagram.com/reel/ABC123"
    assert result.failure_reason == "auth"
    assert repository.inserted == []


def test_fetch_returns_resolution_failure_without_cache_lookup_or_download(
    tmp_path: Path,
) -> None:
    downloader = FakeDownloader(MediaDownloadResult(media=None), unresolved=True)
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.media is None
    assert result.failure_reason == "unsupported"
    assert repository.lookup is None
    assert downloader.download_requests == []


def test_fetch_handles_resolution_error_without_cache_lookup_or_download(
    tmp_path: Path,
) -> None:
    downloader = FakeDownloader(
        MediaDownloadResult(media=None),
        resolution_error=ResolutionError("https://example.com/bad", "auth"),
    )
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.media is None
    assert result.url == "https://example.com/bad"
    assert result.failure_reason == "auth"
    assert repository.lookup is None
    assert downloader.download_requests == []


def test_fetch_rejects_identity_mismatch_as_unknown_failure(tmp_path: Path) -> None:
    mismatched = make_media(str(tmp_path / "other.mp4"))
    mismatched.provider_item_id = "OTHER"
    mismatched.id = "instagram:reel:OTHER"
    downloader = FakeDownloader(MediaDownloadResult(media=mismatched))
    repository = FakeRepository(cached=None)
    service = MediaFetchService(repository, output_dir=tmp_path)

    result = service.fetch(make_candidate(downloader))

    assert result.media is None
    assert result.failure_reason == "unknown"
    assert repository.inserted == []
