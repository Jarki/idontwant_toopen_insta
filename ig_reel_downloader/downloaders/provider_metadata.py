from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def normalize_provider_metadata(
    source: Mapping[str, Any],
    *,
    counters: Iterable[str] = (),
    strings: Iterable[str] = (),
    string_lists: Iterable[str] = (),
    numbers: Iterable[str] = (),
) -> dict[str, Any]:
    """Copy useful provider metadata without inventing values for missing fields."""
    metadata: dict[str, Any] = {}
    for key in counters:
        value = optional_nonnegative_int(source.get(key))
        if value is not None:
            metadata[key] = value
    for key in strings:
        value = source.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    for key in string_lists:
        value = source.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            metadata[key] = list(value)
    for key in numbers:
        value = source.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and value >= 0
        ):
            metadata[key] = value
    return metadata


def optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
