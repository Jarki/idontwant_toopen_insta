"""Fixed-operation repository boundary for the private Error API."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ErrorFilters,
    ErrorGroup,
    ErrorGroupPage,
    ErrorNote,
    ErrorPatch,
    NotePage,
    OccurrencePage,
    PageRequest,
    ReproductionPage,
)


class ErrorRepository(Protocol):
    """Repository operations intentionally accept no SQL or ordering fragments."""

    def health(self) -> None: ...

    def list_groups(
        self, filters: ErrorFilters, page: PageRequest
    ) -> ErrorGroupPage: ...

    def get_group_for_occurrence(self, occurrence_id: int) -> ErrorGroup | None: ...

    def list_occurrences(
        self, occurrence_id: int, page: PageRequest
    ) -> OccurrencePage | None: ...

    def list_reproduction_cases(
        self, occurrence_id: int, page: PageRequest
    ) -> ReproductionPage | None: ...

    def list_notes(self, occurrence_id: int, page: PageRequest) -> NotePage | None: ...

    def update_group(
        self, occurrence_id: int, patch: ErrorPatch
    ) -> ErrorGroup | None: ...

    def add_note(
        self, occurrence_id: int, note: str, actor: str
    ) -> ErrorNote | None: ...
