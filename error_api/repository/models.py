"""Strict public models and fixed repository inputs for the private Error API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Status = Literal["new", "investigating", "fixing", "monitoring", "resolved", "ignored"]
Severity = Literal["ERROR", "CRITICAL"]
BoundedText = Annotated[str, Field(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorFilters(StrictModel):
    status: Status | None = None
    provider: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    severity: Severity | None = None
    event_code: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    component: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    release: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    seen_from: AwareDatetime | None = None
    seen_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> ErrorFilters:
        if self.seen_from and self.seen_to and self.seen_from > self.seen_to:
            raise ValueError("seen_from must not be later than seen_to")
        return self


class PageRequest(StrictModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0


class ErrorGroup(StrictModel):
    id: int
    display_name: str
    event_code: str
    exception_type: str | None
    status: Status
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    occurrence_count: int
    first_release: str | None
    last_release: str | None
    linked_change: str | None
    fixed_at: AwareDatetime | None
    recurred_after_fix: bool
    first_post_fix_occurrence: str | None = None


class ErrorOccurrence(StrictModel):
    reference: str
    occurred_at: AwareDatetime
    severity: Severity
    logger_name: str
    component: str | None
    exception_type: str | None
    message: str
    traceback: str | None
    provider: str | None
    media_kind: str | None
    release: str | None


class ReproductionCase(StrictModel):
    reference: str
    display_name: str
    occurred_at: AwareDatetime
    submitted_url: str | None
    normalized_url: str | None
    provider: str | None
    media_kind: str | None
    component: str | None
    release: str | None


class ErrorNote(StrictModel):
    id: int
    note: str
    actor: str
    created_at: AwareDatetime


class ErrorGroupPage(StrictModel):
    items: list[ErrorGroup]
    next_cursor: str | None


class OccurrencePage(StrictModel):
    items: list[ErrorOccurrence]
    next_cursor: str | None


class ReproductionPage(StrictModel):
    items: list[ReproductionCase]
    next_cursor: str | None


class NotePage(StrictModel):
    items: list[ErrorNote]
    next_cursor: str | None


class ErrorPatch(StrictModel):
    display_name: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    status: Status | None = None
    linked_change: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    fixed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_change(self) -> ErrorPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class NoteCreate(StrictModel):
    note: Annotated[str, Field(min_length=1, max_length=4000)]
