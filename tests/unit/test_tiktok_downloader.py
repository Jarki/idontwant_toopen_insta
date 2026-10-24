import sys
from contextlib import AbstractContextManager

import pytest
from yt_dlp.utils import DownloadError, ExtractorError

from ig_reel_downloader.downloaders.base import ProviderItemRef, ResolutionError
from ig_reel_downloader.downloaders.tiktok import TikTokDownloader


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
