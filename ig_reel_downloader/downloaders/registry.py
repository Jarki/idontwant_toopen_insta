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
        kept: list[_Candidate] = []
        for candidate in ordered:
            overlapping_indexes = [
                index
                for index, existing in enumerate(kept)
                if _overlaps(existing.match, candidate.match)
            ]
            if not overlapping_indexes:
                kept.append(candidate)
                continue

            replacement = candidate
            for index in overlapping_indexes:
                replacement = _prefer_candidate(kept[index], replacement)
            for index in reversed(overlapping_indexes):
                del kept[index]
            kept.append(replacement)
            kept.sort(key=lambda item: (item.match.start, item.match.end))
        return sorted(kept, key=lambda item: (item.match.start, item.match.end))


def _overlaps(left: UrlMatch, right: UrlMatch) -> bool:
    return left.start < right.end and right.start < left.end


def _prefer_candidate(left: _Candidate, right: _Candidate) -> _Candidate:
    if left.registration_index != right.registration_index:
        return left if left.registration_index < right.registration_index else right
    left_len = left.match.end - left.match.start
    right_len = right.match.end - right.match.start
    if left_len != right_len:
        return left if left_len > right_len else right
    return (
        left
        if (left.match.start, left.match.end)
        <= (
            right.match.start,
            right.match.end,
        )
        else right
    )
