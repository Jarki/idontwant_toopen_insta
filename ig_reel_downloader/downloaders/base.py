from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from ig_reel_downloader.repository.models import MediaItem

DownloadFailureReason = Literal["auth", "unsupported", "unknown"]


class ResolutionError(Exception):
    def __init__(
        self,
        url: str,
        failure_reason: DownloadFailureReason = "unknown",
    ) -> None:
        super().__init__(url)
        self.url = url
        self.failure_reason = failure_reason


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
class UrlCandidate:
    url: str
    start: int
    end: int
    downloader: Downloader
    provider: str
    link_type: str
    normalized_url: str | None = None
    local_ref: ProviderItemRef | None = None


@dataclass(frozen=True)
class ResolvedMediaRequest:
    url: str
    downloader: Downloader
    provider_item_ref: ProviderItemRef
    normalized_url: str | None = None
    info: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResolveResult:
    request: ResolvedMediaRequest | None
    skipped: bool = False
    failure_reason: DownloadFailureReason | None = None


@dataclass(frozen=True)
class MediaDownloadResult:
    media: MediaItem | None
    failure_reason: DownloadFailureReason | None = None


@dataclass(frozen=True)
class UrlMatch:
    url: str
    start: int
    end: int
    downloader: Any
    normalized_url: str | None = None


@dataclass(frozen=True)
class ResolvedUrlMatch:
    url: str
    start: int
    end: int
    downloader: Any
    provider_item_ref: ProviderItemRef
    normalized_url: str | None = None


class Downloader(Protocol):
    provider: str
    media_kind: str

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        raise NotImplementedError

    def resolve(self, candidate: UrlCandidate) -> ResolveResult:
        raise NotImplementedError

    def download(
        self,
        request: ResolvedMediaRequest,
        context: DownloadContext,
    ) -> MediaDownloadResult:
        raise NotImplementedError
