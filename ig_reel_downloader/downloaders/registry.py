from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .base import Downloader, ResolvedUrlMatch, UrlMatch


@dataclass(frozen=True)
class _Candidate:
    match: UrlMatch
    registration_index: int


class DownloaderRegistry:
    def __init__(self, downloaders: Sequence[Downloader]) -> None:
        self._downloaders = list(downloaders)

    def extract_matches(self, text: str) -> list[ResolvedUrlMatch]:
        candidates: list[_Candidate] = []
        for index, downloader in enumerate(self._downloaders):
            for match in downloader.extract_urls(text):
                candidates.append(_Candidate(match=match, registration_index=index))

        kept = self._filter_overlaps(candidates)
        resolved: list[ResolvedUrlMatch] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in kept:
            match = candidate.match
            ref = match.downloader.get_provider_item_ref(match.url)
            if ref is None:
                continue
            key = (ref.provider, ref.media_kind, ref.provider_item_id)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                ResolvedUrlMatch(
                    url=match.url,
                    start=match.start,
                    end=match.end,
                    downloader=match.downloader,
                    provider_item_ref=ref,
                    normalized_url=match.normalized_url,
                )
            )
        return resolved

    def _filter_overlaps(self, candidates: list[_Candidate]) -> list[_Candidate]:
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                candidate.match.start,
                candidate.match.end,
                candidate.registration_index,
            ),
        )
        kept: list[_Candidate] = [ordered[0]] if ordered else []
        for candidate in ordered[1:]:
            if any(_overlaps(existing.match, candidate.match) for existing in kept):
                overlapping = [
                    f"{existing.match.downloader.__class__.__name__}:{existing.match.url}@[{existing.match.start},{existing.match.end})"
                    for existing in kept
                    if _overlaps(existing.match, candidate.match)
                ] + [
                    f"{candidate.match.downloader.__class__.__name__}:{candidate.match.url}@[{candidate.match.start},{candidate.match.end})"
                ]
                msg = f"Overlapping URL matches detected: {' vs '.join(overlapping)}"
                raise ValueError(msg)
            kept.append(candidate)
        return kept


def _overlaps(left: UrlMatch, right: UrlMatch) -> bool:
    return left.start < right.end and right.start < left.end
