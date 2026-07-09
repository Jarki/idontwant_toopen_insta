from dataclasses import dataclass

import pytest

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    ResolvedMediaRequest,
    ResolveResult,
    UrlCandidate,
)
from ig_reel_downloader.downloaders.registry import DownloaderRegistry


@dataclass
class FakeDownloader:
    provider: str
    media_kind: str
    matches: list[tuple[str, int, int, str | None]]

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        return [
            UrlCandidate(
                url=url,
                start=start,
                end=end,
                downloader=self,
                provider=self.provider,
                link_type=self.media_kind,
                local_ref=ProviderItemRef(self.provider, self.media_kind, item_id)
                if item_id is not None
                else None,
            )
            for url, start, end, item_id in self.matches
        ]

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        if candidate.local_ref is None:
            return ResolveResult(request=None, failure_reason="unsupported")
        return ResolveResult(
            request=ResolvedMediaRequest(
                url=candidate.url,
                downloader=self,
                provider_item_ref=candidate.local_ref,
                normalized_url=candidate.normalized_url,
            )
        )

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        return MediaDownloadResult(media=None, failure_reason="unknown")


def test_registry_preserves_message_order_after_provider_iteration() -> None:
    later_provider = FakeDownloader("later", "clip", [("later-url", 20, 29, "2")])
    earlier_text_match = FakeDownloader("earlier", "clip", [("early-url", 5, 14, "1")])
    registry = DownloaderRegistry([later_provider, earlier_text_match])

    candidates = registry.extract_candidates("text with multiple urls")

    assert [candidate.url for candidate in candidates] == ["early-url", "later-url"]


def test_registry_raises_on_overlapping_matches_from_different_downloaders() -> None:
    first = FakeDownloader("first", "clip", [("first-url", 0, 20, "first")])
    second = FakeDownloader("second", "clip", [("second-url", 5, 25, "second")])
    registry = DownloaderRegistry([first, second])

    with pytest.raises(ValueError, match="Overlapping URL matches detected"):
        registry.extract_candidates("overlap text")


def test_registry_raises_on_overlapping_matches_from_same_downloader() -> None:
    downloader = FakeDownloader(
        "same",
        "clip",
        [("short", 0, 5, "short"), ("longer", 0, 10, "longer")],
    )
    registry = DownloaderRegistry([downloader])

    with pytest.raises(ValueError, match="Overlapping URL matches detected"):
        registry.extract_candidates("overlap text")


def test_registry_deduplicates_by_provider_identity() -> None:
    downloader = FakeDownloader(
        "instagram",
        "reel",
        [("first-url", 0, 9, "ABC"), ("duplicate-url", 20, 33, "ABC")],
    )
    registry = DownloaderRegistry([downloader])

    candidates = registry.extract_candidates("duplicate text")

    assert [candidate.url for candidate in candidates] == ["first-url"]


def test_registry_returns_candidates_without_requiring_identity() -> None:
    unresolved = FakeDownloader(
        "tiktok",
        "share",
        [("https://vm.tiktok.com/abc/", 5, 31, None)],
    )
    registry = DownloaderRegistry([unresolved])

    candidates = registry.extract_candidates("see https://vm.tiktok.com/abc/ now")

    assert len(candidates) == 1
    assert candidates[0].url == "https://vm.tiktok.com/abc/"
    assert candidates[0].link_type == "share"
    assert candidates[0].local_ref is None


def test_registry_deduplicates_only_when_local_identity_exists() -> None:
    downloader = FakeDownloader(
        "instagram",
        "reel",
        [("first-url", 0, 9, "ABC"), ("duplicate-url", 20, 33, "ABC")],
    )
    registry = DownloaderRegistry([downloader])

    candidates = registry.extract_candidates("duplicate text")

    assert [candidate.url for candidate in candidates] == ["first-url"]


def test_extract_matches_compatibility_filters_unresolvable_candidates() -> None:
    downloader = FakeDownloader("instagram", "reel", [("bad-url", 0, 7, None)])
    registry = DownloaderRegistry([downloader])

    assert registry.extract_matches("bad-url") == []
