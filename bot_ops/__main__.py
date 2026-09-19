"""Command-line interface for private Error API operations."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .client import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ErrorApiClient,
    ServerError,
    TransportError,
)

EXIT_CONFIG = 2
EXIT_API = 3
EXIT_AUTH = 4
EXIT_AUTHZ = 5
EXIT_TRANSPORT = 6
EXIT_SERVER = 7
_STATUSES = ("new", "investigating", "fixing", "monitoring", "resolved", "ignored")
_DURATION = re.compile(r"([1-9][0-9]{0,3})([mhd])\Z")
_MAX_SINCE_SECONDS = 365 * 24 * 60 * 60


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot-ops")
    areas = parser.add_subparsers(dest="area", required=True)
    errors = areas.add_parser("errors", help="inspect and triage errors")
    commands = errors.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list")
    for name in (
        "status",
        "provider",
        "severity",
        "event-code",
        "component",
        "release",
        "seen-from",
        "seen-to",
    ):
        listing.add_argument("--" + name)
    listing.add_argument("--since", type=_relative_duration)
    _add_page(listing)

    show = commands.add_parser("show")
    show.add_argument("reference")
    similar = commands.add_parser("similar")
    similar.add_argument("reference")
    _add_page(similar)
    repro = commands.add_parser("repro")
    repro.add_argument("reference")
    _add_page(repro)
    rename = commands.add_parser("rename")
    rename.add_argument("reference")
    rename.add_argument("display_name")
    status = commands.add_parser("status")
    status.add_argument("reference")
    status.add_argument("status", choices=_STATUSES)
    note = commands.add_parser("note")
    note.add_argument("reference")
    note.add_argument("note")
    link = commands.add_parser("link")
    link.add_argument("reference")
    link.add_argument("change", nargs="?")
    fixed = commands.add_parser("mark-fixed")
    fixed.add_argument("reference")
    fixed.add_argument("--at", choices=("now",), required=True)
    return parser


def _relative_duration(value: str) -> dt.timedelta:
    match = _DURATION.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("must be a duration such as 24h")
    amount = int(match.group(1))
    seconds_per_unit = {"m": 60, "h": 3600, "d": 86400}
    seconds = amount * seconds_per_unit[match.group(2)]
    if seconds > _MAX_SINCE_SECONDS:
        raise argparse.ArgumentTypeError("duration must not exceed 365d")
    return dt.timedelta(seconds=seconds)


def _add_page(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--limit", type=int, choices=range(1, 101), default=50, metavar="1..100"
    )
    parser.add_argument("--cursor")


def _safe(value: str) -> str:
    output: list[str] = []
    for character in value:
        code = ord(character)
        if character == "\n":
            output.append("\\n")
        elif character == "\r":
            output.append("\\r")
        elif character == "\t":
            output.append("\\t")
        elif code == 0x1B:
            output.append("\\x1b")
        elif unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            output.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
        else:
            output.append(character)
    return "".join(output)


def _render(value: object, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key in sorted(value):
            item = value[key]
            safe_key = _safe(str(key))
            if isinstance(item, (Mapping, list)):
                lines.append(f"{prefix}{safe_key}:")
                lines.extend(_render(item, indent + 2))
            else:
                lines.append(f"{prefix}{safe_key}: {_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, Mapping):
                lines.append(prefix + "-")
                lines.extend(_render(item, indent + 2))
            else:
                lines.append(prefix + "- " + _scalar(item))
        return lines or [prefix + "[]"]
    return [prefix + _scalar(value)]


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return _safe(str(value))


def _client_from_environment() -> ErrorApiClient:
    url = os.getenv("ERROR_API_URL", "").strip()
    key = os.getenv("ERROR_API_KEY", "").strip()
    if not url or not key:
        raise ValueError("ERROR_API_URL and ERROR_API_KEY must be set")
    return ErrorApiClient(url, key)


def _dispatch(client: ErrorApiClient, args: argparse.Namespace) -> dict[str, Any]:
    command: str = args.command
    if command == "list":
        names = (
            "status",
            "provider",
            "severity",
            "event_code",
            "component",
            "release",
            "seen_from",
            "seen_to",
            "limit",
            "cursor",
        )
        filters = {name: getattr(args, name) for name in names}
        if args.since is not None:
            if filters["seen_from"] is not None:
                raise ApiError("--since and --seen-from are mutually exclusive")
            since = dt.datetime.now(dt.UTC) - args.since
            filters["seen_from"] = since.isoformat().replace("+00:00", "Z")
        return client.list_errors(filters)
    if command == "show":
        return client.show(args.reference)
    if command == "similar":
        return client.similar(args.reference, limit=args.limit, cursor=args.cursor)
    if command == "repro":
        return client.reproduction_cases(
            args.reference, limit=args.limit, cursor=args.cursor
        )
    if command == "rename":
        return client.rename(args.reference, args.display_name)
    if command == "status":
        return client.set_status(args.reference, args.status)
    if command == "note":
        return client.add_note(args.reference, args.note)
    if command == "link":
        return client.link(args.reference, args.change)
    if command == "mark-fixed":
        fixed_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
        return client.mark_fixed(args.reference, fixed_at)
    raise AssertionError("unreachable command")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        client = _client_from_environment()
    except ValueError:
        print("configuration error: invalid Error API configuration", file=sys.stderr)
        return EXIT_CONFIG
    try:
        result = _dispatch(client, args)
    except AuthenticationError as error:
        print(_safe(str(error)), file=sys.stderr)
        return EXIT_AUTH
    except AuthorizationError as error:
        print(_safe(str(error)), file=sys.stderr)
        return EXIT_AUTHZ
    except TransportError as error:
        print(_safe(str(error)), file=sys.stderr)
        return EXIT_TRANSPORT
    except ServerError as error:
        print(_safe(str(error)), file=sys.stderr)
        return EXIT_SERVER
    except ApiError as error:
        print(_safe(str(error)), file=sys.stderr)
        return EXIT_API
    print("\n".join(_render(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
