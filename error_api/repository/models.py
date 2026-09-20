"""Strict public models and fixed repository inputs for the private Error API."""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Status = Literal["new", "investigating", "fixing", "monitoring", "resolved", "ignored"]
Severity = Literal["ERROR", "CRITICAL"]
BoundedText = Annotated[str, Field(min_length=1, max_length=256)]
MAX_BIGINT = 9_223_372_036_854_775_807


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
    cursor_time: AwareDatetime | None = None
    cursor_id: Annotated[int, Field(gt=0, le=MAX_BIGINT)] | None = None
    cursor_tiebreaker: Annotated[int, Field(gt=0, le=MAX_BIGINT)] | None = None

    @model_validator(mode="after")
    def validate_cursor(self) -> PageRequest:
        if (self.cursor_time is None) != (self.cursor_id is None):
            raise ValueError("cursor time and id must be provided together")
        if self.cursor_tiebreaker is not None and self.cursor_time is None:
            raise ValueError("cursor tiebreaker requires a cursor")
        return self


def encode_cursor(
    kind: str, when: dt.datetime, identifier: int, tiebreaker: int | None = None
) -> str:
    values: list[str | int] = [
        kind,
        when.isoformat().replace("+00:00", "Z"),
        identifier,
    ]
    if tiebreaker is not None:
        values.append(tiebreaker)
    return (
        base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(
    value: str, kind: str, *, tiebreaker: bool = False
) -> tuple[dt.datetime, int, int | None]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
        expected_length = 4 if tiebreaker else 3
        if (
            not isinstance(decoded, list)
            or len(decoded) != expected_length
            or decoded[0] != kind
            or not isinstance(decoded[1], str)
            or not isinstance(decoded[2], int)
            or isinstance(decoded[2], bool)
            or not 1 <= decoded[2] <= MAX_BIGINT
            or (
                tiebreaker
                and (
                    not isinstance(decoded[3], int)
                    or isinstance(decoded[3], bool)
                    or not 1 <= decoded[3] <= MAX_BIGINT
                )
            )
        ):
            raise ValueError
        when = dt.datetime.fromisoformat(decoded[1].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError
        tie = decoded[3] if tiebreaker else None
        if encode_cursor(kind, when, decoded[2], tie) != value:
            raise ValueError
        return when, decoded[2], tie
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise ValueError("invalid cursor") from error


class ErrorGroup(StrictModel):
    reference: str
    display_name: Annotated[str, Field(max_length=256)]
    event_code: Annotated[str, Field(max_length=128)]
    exception_type: Annotated[str, Field(max_length=256)] | None
    status: Status
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    occurrence_count: int
    first_release: Annotated[str, Field(max_length=128)] | None
    last_release: Annotated[str, Field(max_length=128)] | None
    linked_change: Annotated[str, Field(max_length=512)] | None
    fixed_at: AwareDatetime | None
    recurred_after_fix: bool
    first_post_fix_occurrence: str | None = None


class ErrorOccurrence(StrictModel):
    reference: str
    occurred_at: AwareDatetime
    severity: Severity
    logger_name: Annotated[str, Field(max_length=255)]
    component: Annotated[str, Field(max_length=255)] | None
    exception_type: Annotated[str, Field(max_length=256)] | None
    message: Annotated[str, Field(max_length=8192)]
    traceback: Annotated[str, Field(max_length=65536)] | None
    provider: Annotated[str, Field(max_length=64)] | None
    media_kind: Annotated[str, Field(max_length=64)] | None
    release: Annotated[str, Field(max_length=128)] | None


class ReproductionCase(StrictModel):
    reference: str
    display_name: Annotated[str, Field(max_length=256)]
    occurred_at: AwareDatetime
    submitted_url: Annotated[str, Field(max_length=8192)] | None
    normalized_url: Annotated[str, Field(max_length=8192)] | None
    provider: Annotated[str, Field(max_length=64)] | None
    media_kind: Annotated[str, Field(max_length=64)] | None
    component: Annotated[str, Field(max_length=255)] | None
    release: Annotated[str, Field(max_length=128)] | None


class ErrorNote(StrictModel):
    id: int
    note: Annotated[str, Field(max_length=4000)]
    actor: Annotated[str, Field(max_length=128)]
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
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name may not be null")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status may not be null")
        return self


class NoteCreate(StrictModel):
    note: Annotated[str, Field(min_length=1, max_length=4000)]
