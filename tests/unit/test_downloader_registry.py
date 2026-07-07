from dataclasses import dataclass

from ig_reel_downloader.downloaders.base import (
    DownloadContext,
    MediaDownloadResult,
    ProviderItemRef,
    UrlMatch,
)
from ig_reel_downloader.downloaders.registry import DownloaderRegistry


@dataclass
class FakeDownloader:
    provider: str
    media_kind: str
    matches: list[tuple[str, int, int, str | None]]

    def extract_urls(self, text: str) -> list[UrlMatch]:
        return [
            UrlMatch(url=url, start=start, end=end, downloader=self)
            for url, start, end, _ in self.matches
        ]

    def get_provider_item_ref(self, url: str) -> ProviderItemRef | None:
        for match_url, _, _, item_id in self.matches:
            if match_url == url and item_id is not None:
                return ProviderItemRef(self.provider, self.media_kind, item_id)
        return None

    def download(self, url: str, context: DownloadContext) -> MediaDownloadResult:
        return MediaDownloadResult(media=None, failure_reason="unknown")


def test_registry_preserves_message_order_after_provider_iteration() -> None:
    later_provider = FakeDownloader("later", "clip", [("later-url", 20, 29, "2")])
    earlier_text_match = FakeDownloader("earlier", "clip", [("early-url", 5, 14, "1")])
    registry = DownloaderRegistry([later_provider, earlier_text_match])

    matches = registry.extract_matches("text with multiple urls")

    assert [match.url for match in matches] == ["early-url", "later-url"]


def test_registry_prefers_earlier_registered_downloader_for_overlaps() -> None:
    first = FakeDownloader("first", "clip", [("first-url", 0, 20, "first")])
    second = FakeDownloader("second", "clip", [("second-url", 5, 25, "second")])
    registry = DownloaderRegistry([first, second])

    matches = registry.extract_matches("overlap text")

    assert [match.url for match in matches] == ["first-url"]


def test_registry_prefers_longest_same_downloader_overlap() -> None:
    downloader = FakeDownloader(
        "same",
        "clip",
        [("short", 0, 5, "short"), ("longer", 0, 10, "longer")],
    )
    registry = DownloaderRegistry([downloader])

    matches = registry.extract_matches("overlap text")

    assert [match.url for match in matches] == ["longer"]


def test_registry_deduplicates_by_provider_identity() -> None:
    downloader = FakeDownloader(
        "instagram",
        "reel",
        [("first-url", 0, 9, "ABC"), ("duplicate-url", 20, 33, "ABC")],
    )
    registry = DownloaderRegistry([downloader])

    matches = registry.extract_matches("duplicate text")

    assert [match.url for match in matches] == ["first-url"]


def test_registry_filters_unresolvable_matches() -> None:
    downloader = FakeDownloader("instagram", "reel", [("bad-url", 0, 7, None)])
    registry = DownloaderRegistry([downloader])

    assert registry.extract_matches("bad-url") == []
