from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ig_reel_downloader.repository.models import MediaItem

DownloadFailureReason = Literal["auth", "unsupported", "unknown"]


@dataclass(frozen=True)
class ProviderItemRef:
    provider: str
    media_kind: str
    provider_item_id: str

    @property
    def media_id(self) -> str:
        return f"{self.provider}:{self.media_kind}:{self.provider_item_id}"


@dataclass(frozen=True)
class DownloadContext:
    output_dir: Path


@dataclass(frozen=True)
class MediaDownloadResult:
    media: MediaItem | None
    failure_reason: DownloadFailureReason | None = None


@dataclass(frozen=True)
class UrlMatch:
    url: str
    start: int
    end: int
    downloader: Downloader
    normalized_url: str | None = None


@dataclass(frozen=True)
class ResolvedUrlMatch:
    url: str
    start: int
    end: int
    downloader: Downloader
    provider_item_ref: ProviderItemRef
    normalized_url: str | None = None


class Downloader(Protocol):
    provider: str
    media_kind: str

    def extract_urls(self, text: str) -> list[UrlMatch]:
        raise NotImplementedError

    def get_provider_item_ref(self, url: str) -> ProviderItemRef | None:
        raise NotImplementedError

    def download(
        self,
        url: str,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        raise NotImplementedError
