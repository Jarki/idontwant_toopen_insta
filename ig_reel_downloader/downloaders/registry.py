from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .base import Downloader, ResolvedUrlMatch, UrlCandidate


@dataclass(frozen=True)
class _Candidate:
    candidate: UrlCandidate
    registration_index: int


class DownloaderRegistry:
    def __init__(self, downloaders: Sequence[Downloader]) -> None:
        self._downloaders = list(downloaders)

    def extract_candidates(self, text: str) -> list[UrlCandidate]:
        candidates: list[_Candidate] = []
        for index, downloader in enumerate(self._downloaders):
            for candidate in downloader.extract_candidates(text):
                candidates.append(
                    _Candidate(candidate=candidate, registration_index=index)
                )

        kept = self._filter_overlaps(candidates)
        result: list[UrlCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for wrapper in kept:
            candidate = wrapper.candidate
            if candidate.local_ref is not None:
                key = (
                    candidate.local_ref.provider,
                    candidate.local_ref.media_kind,
                    candidate.local_ref.provider_item_id,
                )
                if key in seen:
                    continue
                seen.add(key)
            result.append(candidate)
        return result

    def extract_matches(self, text: str) -> list[ResolvedUrlMatch]:
        resolved: list[ResolvedUrlMatch] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in self.extract_candidates(text):
            result = candidate.downloader.resolve(candidate)
            request = result.request
            if request is None:
                continue
            ref = request.provider_item_ref
            key = (ref.provider, ref.media_kind, ref.provider_item_id)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                ResolvedUrlMatch(
                    url=request.url,
                    start=candidate.start,
                    end=candidate.end,
                    downloader=request.downloader,
                    provider_item_ref=ref,
                    normalized_url=request.normalized_url,
                )
            )
        return resolved

    def _filter_overlaps(self, candidates: list[_Candidate]) -> list[_Candidate]:
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                candidate.candidate.start,
                candidate.candidate.end,
                candidate.registration_index,
            ),
        )
        kept: list[_Candidate] = [ordered[0]] if ordered else []
        for candidate in ordered[1:]:
            if any(
                _overlaps(existing.candidate, candidate.candidate) for existing in kept
            ):
                overlapping = [
                    f"{existing.candidate.downloader.__class__.__name__}:{existing.candidate.url}@[{existing.candidate.start},{existing.candidate.end})"
                    for existing in kept
                    if _overlaps(existing.candidate, candidate.candidate)
                ] + [
                    f"{candidate.candidate.downloader.__class__.__name__}:{candidate.candidate.url}@[{candidate.candidate.start},{candidate.candidate.end})"
                ]
                msg = f"Overlapping URL matches detected: {' vs '.join(overlapping)}"
                raise ValueError(msg)
            kept.append(candidate)
        return kept


def _overlaps(left: UrlCandidate, right: UrlCandidate) -> bool:
    return left.start < right.end and right.start < left.end
