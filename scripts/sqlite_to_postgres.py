#!/usr/bin/env python3
r"""SQLite-to-PostgreSQL transfer script for ig-reel-downloader.

Transfers ``media_items``, ``media_assets``, ``judgmental_animations``,
and compatible legacy ``reels`` rows from a SQLite database to a PostgreSQL
target using the neutral schema records.

Usage::

    uv run python scripts/sqlite_to_postgres.py \
        --sqlite-path /app/data/reels.db \
        --postgres-url "$DATABASE_URL" \
        --upgrade-schema \
        --verify
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, create_engine, inspect, text

EXPECTED_ALEMBIC_HEAD = "20260715_0004"

REQUIRED_SOURCE_TABLES = frozenset(
    {
        "alembic_version",
        "media_items",
        "media_assets",
        "judgmental_animations",
    }
)

REQUIRED_REELS_COLUMNS = (
    "id",
    "title",
    "description",
    "filepath",
    "url",
    "like_count",
    "created_at",
    "comments",
)

MEDIA_ITEM_COLUMNS = (
    "id",
    "provider",
    "media_kind",
    "provider_item_id",
    "original_url",
    "title",
    "description",
    "metadata_json",
    "created_at",
    "updated_at",
)

MEDIA_ASSET_COLUMNS = (
    "id",
    "media_item_id",
    "asset_index",
    "asset_type",
    "filepath",
    "mime_type",
    "width",
    "height",
    "duration_seconds",
    "file_size_bytes",
    "created_at",
)

ANIMATION_COLUMNS = (
    "id",
    "file_id",
    "file_unique_id",
    "created_at",
    "updated_at",
)

VALID_ASSET_TYPES = frozenset({"video", "image"})

# Tables that belong to the application (not Alembic metadata).
APPLICATION_TABLES = ("media_items", "media_assets", "judgmental_animations", "reels")


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transfer SQLite data to PostgreSQL for ig-reel-downloader",
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to the SQLite source database (opened read-only)",
    )
    parser.add_argument(
        "--postgres-url",
        default=None,
        help="PostgreSQL target URL (postgresql+psycopg://). "
        "Falls back to DB_MIGRATION_URL, then DATABASE_URL.",
    )
    parser.add_argument(
        "--upgrade-schema",
        action="store_true",
        help="Run Alembic migrations on the PostgreSQL target before transfer",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run post-commit sequence probes and additional validation",
    )
    parser.add_argument(
        "--skip-legacy-reels",
        action="store_true",
        help="Skip the legacy reels table entirely instead of failing on dirty rows",
    )
    parser.add_argument(
        "--reset-target",
        action="store_true",
        help="Clear existing application data on the PostgreSQL target before transfer",
    )
    return parser.parse_args(argv)


# ── URL validation ─────────────────────────────────────────────────────────


def resolve_target_url(url: str | None) -> str:
    """Validate and return the PostgreSQL target URL.

    Rejects non-psycopg (e.g. psycopg2) and non-postgres URLs.
    """
    if not url:
        print(
            "ERROR: No target URL provided. Pass --postgres-url or set "
            "DB_MIGRATION_URL or DATABASE_URL.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not url.startswith("postgresql+psycopg://"):
        scheme_part = url.split("://")[0] if "://" in url else url
        print(
            f"ERROR: Target URL must use the postgresql+psycopg:// dialect. "
            f"Got: {scheme_part!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    return url


# ── helpers ────────────────────────────────────────────────────────────────


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _echo(msg: str) -> None:
    print(msg)


def _sqlite_engine(path: str) -> Any:
    """Create a SQLAlchemy engine for a read-only SQLite source."""
    resolved = str(Path(path).resolve())

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(
            f"file:{resolved}?mode=ro",
            uri=True,
            check_same_thread=False,
        )

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def _alembic_version(engine: Any) -> str | None:
    """Return the current Alembic version string from the database."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT version_num FROM alembic_version"),
        ).fetchone()
    return str(row[0]) if row else None


def _verify_alembic_head(engine: Any, label: str = "source") -> None:
    """Exit if the database Alembic version is not the expected head."""
    version = _alembic_version(engine)
    if version != EXPECTED_ALEMBIC_HEAD:
        _die(
            f"{label.title()} Alembic version is {version!r}, "
            f"expected {EXPECTED_ALEMBIC_HEAD!r}",
        )
    _echo(f"  Alembic head ({label}): {EXPECTED_ALEMBIC_HEAD}")


# ── source preflight ───────────────────────────────────────────────────────


def _preflight_source(engine: Any) -> dict[str, int]:
    """Verify source has required tables/columns and return row counts."""
    inspector = inspect(engine)
    existing_names = set(inspector.get_table_names())

    missing = REQUIRED_SOURCE_TABLES - existing_names
    if missing:
        _die(
            f"Source database missing required tables: {', '.join(sorted(missing))}",
        )

    # Verify required columns on each application table.
    required_schemas: dict[str, tuple[str, ...]] = {
        "media_items": MEDIA_ITEM_COLUMNS,
        "media_assets": MEDIA_ASSET_COLUMNS,
        "judgmental_animations": ANIMATION_COLUMNS,
    }
    for table, expected_cols in required_schemas.items():
        actual_cols = {c["name"] for c in inspector.get_columns(table)}
        missing_cols = set(expected_cols) - actual_cols
        if missing_cols:
            _die(
                f"Source table {table} is missing required columns: "
                f"{', '.join(sorted(missing_cols))}",
            )

    _echo("  Tables and columns verified")

    # Return row counts for reporting.
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in ("media_items", "media_assets", "judgmental_animations", "reels"):
            if table in existing_names:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                counts[table] = result.scalar() or 0

    return counts


# ── data reading ───────────────────────────────────────────────────────────


def _read_table(
    engine: Any, table: str, columns: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Read all rows from a source table, ordered by primary key."""
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {columns[0]}"),
        )
        return [dict(row._mapping) for row in result]


def _read_reels(engine: Any) -> list[dict[str, Any]]:
    """Read the reels table, if it exists."""
    if "reels" not in inspect(engine).get_table_names():
        return []
    return _read_table(engine, "reels", REQUIRED_REELS_COLUMNS)


# ── validation ─────────────────────────────────────────────────────────────


def _validate_asset_types(assets: list[dict[str, Any]]) -> list[str]:
    """Return error messages for invalid asset types."""
    errors: list[str] = []
    for row in assets:
        at = row.get("asset_type")
        if at not in VALID_ASSET_TYPES:
            errors.append(
                f"media_assets id={row.get('id')!r}: invalid asset_type={at!r}",
            )
    return errors


def _validate_metadata_json(items: list[dict[str, Any]]) -> list[str]:
    """Return error messages for invalid metadata_json values."""
    errors: list[str] = []
    for row in items:
        raw = row.get("metadata_json")
        if raw is None:
            errors.append(f"media_items id={row.get('id')!r}: metadata_json is NULL")
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            errors.append(
                f"media_items id={row.get('id')!r}: "
                f"metadata_json is not valid JSON: {exc}",
            )
            continue
        if not isinstance(parsed, dict):
            errors.append(
                f"media_items id={row.get('id')!r}: metadata_json is not a JSON object",
            )
    return errors


def _is_int_in_range(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _is_valid_timestamp(value: Any) -> bool:
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError:
            return False
    else:
        return False
    return parsed.tzinfo is None or parsed.utcoffset() is None


def _validate_generic_rows(
    items: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    animations: list[dict[str, Any]],
) -> list[str]:
    """Validate portable types, relationships, ranges, and uniqueness."""
    errors: list[str] = []
    item_ids = {row.get("id") for row in items}
    identities: set[tuple[Any, Any, Any]] = set()
    for row in items:
        identity = (
            row.get("provider"),
            row.get("media_kind"),
            row.get("provider_item_id"),
        )
        if any(not isinstance(value, str) or not value for value in identity):
            errors.append(f"media_items id={row.get('id')!r}: invalid identity")
        elif identity in identities:
            errors.append(f"media_items id={row.get('id')!r}: duplicate identity")
        identities.add(identity)
        for column in ("created_at", "updated_at"):
            if not _is_valid_timestamp(row.get(column)):
                errors.append(
                    f"media_items id={row.get('id')!r}: invalid {column}",
                )

    asset_keys: set[tuple[Any, Any]] = set()
    for row in assets:
        row_id = row.get("id")
        if not _is_int_in_range(row_id, -(2**31), 2**31 - 1):
            errors.append(f"media_assets id={row_id!r}: id outside INTEGER range")
        parent_id = row.get("media_item_id")
        if parent_id not in item_ids:
            errors.append(f"media_assets id={row_id!r}: missing parent media_item")
        asset_index = row.get("asset_index")
        if not _is_int_in_range(asset_index, -(2**31), 2**31 - 1):
            errors.append(f"media_assets id={row_id!r}: invalid asset_index")
        key = (parent_id, asset_index)
        if key in asset_keys:
            errors.append(f"media_assets id={row_id!r}: duplicate asset_index")
        asset_keys.add(key)
        for column in ("width", "height"):
            value = row.get(column)
            if value is not None and not _is_int_in_range(value, -(2**31), 2**31 - 1):
                errors.append(f"media_assets id={row_id!r}: invalid {column}")
        file_size = row.get("file_size_bytes")
        if file_size is not None and (not _is_int_in_range(file_size, 0, 2**63 - 1)):
            errors.append(f"media_assets id={row_id!r}: invalid file_size_bytes")
        if not _is_valid_timestamp(row.get("created_at")):
            errors.append(f"media_assets id={row_id!r}: invalid created_at")

    file_ids: set[Any] = set()
    unique_ids: set[Any] = set()
    for row in animations:
        row_id = row.get("id")
        if not _is_int_in_range(row_id, -(2**31), 2**31 - 1):
            errors.append(
                f"judgmental_animations id={row_id!r}: id outside INTEGER range"
            )
        file_id = row.get("file_id")
        unique_id = row.get("file_unique_id")
        if not isinstance(file_id, str) or not file_id:
            errors.append(f"judgmental_animations id={row_id!r}: invalid file_id")
        elif file_id in file_ids:
            errors.append(f"judgmental_animations id={row_id!r}: duplicate file_id")
        file_ids.add(file_id)
        if unique_id is not None:
            if not isinstance(unique_id, str) or not unique_id:
                errors.append(
                    f"judgmental_animations id={row_id!r}: invalid file_unique_id"
                )
            elif unique_id in unique_ids:
                errors.append(
                    f"judgmental_animations id={row_id!r}: duplicate file_unique_id"
                )
            unique_ids.add(unique_id)
        for column in ("created_at", "updated_at"):
            if not _is_valid_timestamp(row.get(column)):
                errors.append(f"judgmental_animations id={row_id!r}: invalid {column}")
    return errors


def _classify_reels(
    reels: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate PostgreSQL-compatible legacy rows from dirty rows."""
    clean: list[dict[str, Any]] = []
    dirty: list[dict[str, Any]] = []

    for row in reels:
        issues: list[str] = []
        if not isinstance(row.get("id"), str) or not row["id"]:
            issues.append("id is NULL, empty, or not text")
        for column in ("title", "filepath", "url"):
            value = row.get(column)
            if value is None:
                issues.append(f"{column} is NULL")
            elif not isinstance(value, str):
                issues.append(f"{column} is not text")
        description = row.get("description")
        if description is not None and not isinstance(description, str):
            issues.append("description is not text")
        like_count = row.get("like_count")
        if like_count is None:
            issues.append("like_count is NULL")
        elif not _is_int_in_range(like_count, -(2**31), 2**31 - 1):
            issues.append("like_count is outside INTEGER range")
        if not _is_valid_timestamp(row.get("created_at")):
            issues.append("created_at is not a portable timestamp")
        comments = row.get("comments")
        if not isinstance(comments, str):
            issues.append("comments is not text")
        else:
            try:
                parsed_comments = json.loads(comments)
            except json.JSONDecodeError:
                issues.append("comments is not valid JSON")
            else:
                if not isinstance(parsed_comments, list):
                    issues.append("comments is not a JSON array")

        if issues:
            dirty.append({"id": row.get("id"), "issues": issues})
        else:
            clean.append(row)

    return clean, dirty


# ── target preflight ───────────────────────────────────────────────────────


def _target_preflight(engine: Any, reset: bool) -> None:
    """Refuse existing application rows unless an explicit reset is selected."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing = set(APPLICATION_TABLES) - existing
    if missing:
        _die(
            f"Target database missing application tables: {', '.join(sorted(missing))}"
        )
    present_app = [table for table in APPLICATION_TABLES if table in existing]

    with engine.connect() as conn:
        row_counts = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            for table in present_app
        }

    non_empty = {table: count for table, count in row_counts.items() if count > 0}
    if non_empty and not reset:
        details = "; ".join(
            f"{table}: {count} rows" for table, count in sorted(non_empty.items())
        )
        _die(
            f"Target database has existing application rows. "
            f"Use --reset-target to clear. {details}",
        )


def _reset_target(conn: Any) -> None:
    """Reset application tables inside the transfer transaction."""
    existing = set(inspect(conn).get_table_names())
    for table in APPLICATION_TABLES:
        if table in existing:
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))


# ── Alembic upgrade ────────────────────────────────────────────────────────


def _upgrade_target(url: str) -> None:
    """Run Alembic upgrade head on the PostgreSQL target."""
    _echo("  Running Alembic upgrade on target...")
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.attributes["database_url"] = url
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        _die(f"Alembic upgrade failed: {exc}")


# ── row insertion ──────────────────────────────────────────────────────────


def _insert_rows(
    conn: Any,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    """Insert rows into *table* within the current transaction."""
    if not rows:
        return
    col_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    stmt = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    for row in rows:
        conn.execute(text(stmt), row)


# ── verification (in-transaction) ──────────────────────────────────────────


def _canonical_value(column: str, value: Any) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat(sep=" ")
    if isinstance(value, str) and column in {"created_at", "updated_at"}:
        try:
            return datetime.datetime.fromisoformat(value).isoformat(sep=" ")
        except ValueError:
            pass
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _row_checksum(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    """Deterministic SHA-256 of sorted rows across SQLite/PostgreSQL types."""
    h = hashlib.sha256()
    for row in rows:
        canonical = "|".join(
            _canonical_value(column, row.get(column)) for column in columns
        )
        h.update(canonical.encode("utf-8"))
    return h.hexdigest()


def _verify_transfer(conn: Any, source_data: dict[str, Any]) -> None:
    """Verify row counts, data parity, and foreign keys in-transaction."""
    # ── row counts ──
    _echo("  Verifying row counts...")
    for table in ("media_items", "media_assets", "judgmental_animations", "reels"):
        expected = len(source_data.get(table, []))
        actual = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        if expected != actual:
            _die(
                f"Row count mismatch for {table}: expected {expected}, got {actual}",
            )
    _echo("    OK")

    # ── data parity via checksums ──
    _echo("  Verifying data parity...")
    parity_config: list[tuple[str, tuple[str, ...]]] = [
        ("media_items", MEDIA_ITEM_COLUMNS),
        ("media_assets", MEDIA_ASSET_COLUMNS),
        ("judgmental_animations", ANIMATION_COLUMNS),
    ]
    for table, columns in parity_config:
        src_checksum = _row_checksum(source_data[table], columns)
        col_list = ", ".join(columns)
        result = conn.execute(
            text(f"SELECT {col_list} FROM {table} ORDER BY {columns[0]}"),
        )
        tgt_rows = [dict(r._mapping) for r in result]
        tgt_checksum = _row_checksum(tgt_rows, columns)
        if src_checksum != tgt_checksum:
            _die(f"Data parity mismatch for {table}")
    _echo("    OK")

    # ── foreign key integrity ──
    _echo("  Verifying foreign keys...")
    orphan = (
        conn.execute(
            text(
                "SELECT COUNT(*) FROM media_assets ma "
                "LEFT JOIN media_items mi ON ma.media_item_id = mi.id "
                "WHERE mi.id IS NULL",
            ),
        ).scalar()
        or 0
    )
    if orphan:
        _die(f"Found {orphan} orphan media_assets (no parent media_item)")
    _echo("    OK")

    # ── uniqueness / no duplicates on PK ──
    _echo("  Verifying primary key uniqueness...")
    for table, pk in (
        ("media_items", "id"),
        ("media_assets", "id"),
        ("judgmental_animations", "id"),
        ("reels", "id"),
    ):
        # Only check if table has rows.
        count = (
            conn.execute(
                text(f"SELECT COUNT(*) FROM {table}"),
            ).scalar()
            or 0
        )
        if count == 0:
            continue
        dup = (
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM ("
                    f"  SELECT {pk} FROM {table} "
                    f"  GROUP BY {pk} HAVING COUNT(*) > 1"
                    f") AS dups",
                ),
            ).scalar()
            or 0
        )
        if dup:
            _die(f"Found {dup} duplicate {pk} values in {table}")
    _echo("    OK")


# ── post-commit sequence repair and probes ─────────────────────────────────


def _repair_sequences(engine: Any) -> list[str]:
    """Repair PostgreSQL autoincrement sequences after bulk insert."""
    actions: list[str] = []
    sequences = [
        ("media_assets", "id"),
        ("judgmental_animations", "id"),
    ]
    with engine.begin() as conn:
        for table, column in sequences:
            seq_name = f"{table}_{column}_seq"
            next_val = (
                conn.execute(
                    text(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}"),
                ).scalar()
                or 1
            )
            conn.execute(
                text(f"SELECT setval('{seq_name}', :nv, false)"),
                {"nv": next_val},
            )
            actions.append(f"  Set {seq_name} → {next_val}")
    return actions


def _next_probe_key(conn: Any, table: str) -> str:
    index = 0
    while True:
        candidate = f"__transfer_probe__{index}"
        if table == "media_items":
            exists = conn.execute(
                text(
                    "SELECT 1 FROM media_items "
                    "WHERE id = :candidate OR "
                    "(provider = 'probe' AND media_kind = 'probe' "
                    "AND provider_item_id = :candidate)"
                ),
                {"candidate": candidate},
            ).first()
        else:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM judgmental_animations "
                    "WHERE file_id = :candidate OR file_unique_id = :candidate"
                ),
                {"candidate": candidate},
            ).first()
        if exists is None:
            return candidate
        index += 1


def _probe_sequences(engine: Any) -> list[str]:
    """Prove sequences work with disposable rows that are always rolled back."""
    probes: list[str] = []
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            probe_key = _next_probe_key(conn, "media_items")
            conn.execute(
                text(
                    "INSERT INTO media_items "
                    "(id, provider, media_kind, provider_item_id, original_url, "
                    "title, metadata_json, created_at, updated_at) "
                    "VALUES (:probe_key, 'probe', 'probe', :probe_key, "
                    "'https://probe.invalid', 'probe', '{}', NOW(), NOW())"
                ),
                {"probe_key": probe_key},
            )
            conn.execute(
                text(
                    "INSERT INTO media_assets "
                    "(media_item_id, asset_index, asset_type, filepath, created_at) "
                    "VALUES (:probe_key, -1, 'video', '/dev/null', NOW())"
                ),
                {"probe_key": probe_key},
            )
            transaction.rollback()
            probes.append("  media_assets sequence: accepted probe row (rolled back)")
        except BaseException:
            transaction.rollback()
            raise

    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            probe_key = _next_probe_key(conn, "judgmental_animations")
            conn.execute(
                text(
                    "INSERT INTO judgmental_animations "
                    "(file_id, file_unique_id, created_at, updated_at) "
                    "VALUES (:probe_key, :probe_key, NOW(), NOW())"
                ),
                {"probe_key": probe_key},
            )
            transaction.rollback()
            probes.append(
                "  judgmental_animations sequence: accepted probe row (rolled back)",
            )
        except BaseException:
            transaction.rollback()
            raise

    return probes


def _resolve_asset_path(filepath: str, output_path: Path | None) -> Path:
    path = Path(filepath)
    if path.is_absolute() or output_path is None:
        return path

    output_parts = output_path.parts
    for start in range(len(output_parts)):
        prefix = output_parts[start:]
        if path.parts[: len(prefix)] == prefix:
            return output_path / Path(*path.parts[len(prefix) :])
    return output_path / path


def _report_missing_files(
    assets: list[dict[str, Any]],
    output_dir: str = "",
) -> list[str]:
    """List asset filepaths that do not exist on the filesystem (no mutation)."""
    missing: list[str] = []
    seen: set[str] = set()
    output_path = Path(output_dir) if output_dir else None
    for asset in assets:
        filepath = str(asset.get("filepath", ""))
        if not filepath or filepath in seen:
            continue
        seen.add(filepath)
        path = _resolve_asset_path(filepath, output_path)
        if not path.exists():
            missing.append(str(path))
    return missing


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── main ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # ── 1. Validate target URL ──
    postgres_url = resolve_target_url(
        args.postgres_url
        or os.environ.get("DB_MIGRATION_URL")
        or os.environ.get("DATABASE_URL"),
    )

    # ── 2. Open SQLite read-only ──
    sqlite_path = args.sqlite_path
    sqlite_path_obj = Path(sqlite_path)
    if not sqlite_path_obj.is_file():
        _die(f"SQLite source not found: {sqlite_path}")
    sqlite = _sqlite_engine(sqlite_path)
    source_checksum = _file_checksum(sqlite_path_obj)
    _echo(f"Source: {sqlite_path}")
    _echo(f"  Source checksum: {source_checksum}")

    # ── 3. Source preflight ──
    _verify_alembic_head(sqlite, "source")
    source_counts = _preflight_source(sqlite)
    _echo(
        f"  Rows: media_items={source_counts.get('media_items', 0)}, "
        f"media_assets={source_counts.get('media_assets', 0)}, "
        f"judgmental_animations={source_counts.get('judgmental_animations', 0)}, "
        f"reels={source_counts.get('reels', 0)}",
    )

    # ── 4. Read source data ──
    media_items = _read_table(sqlite, "media_items", MEDIA_ITEM_COLUMNS)
    media_assets = _read_table(sqlite, "media_assets", MEDIA_ASSET_COLUMNS)
    animations = _read_table(sqlite, "judgmental_animations", ANIMATION_COLUMNS)
    reels = _read_reels(sqlite)
    sqlite.dispose()
    if _file_checksum(sqlite_path_obj) != source_checksum:
        _die("SQLite source changed while it was being read")

    # ── 5. Validate data ──
    asset_errors = _validate_asset_types(media_assets)
    if asset_errors:
        for err in asset_errors:
            print(f"  INVALID: {err}", file=sys.stderr)
        _die(f"{len(asset_errors)} invalid asset type(s) found")

    meta_errors = _validate_metadata_json(media_items)
    if meta_errors:
        for err in meta_errors:
            print(f"  INVALID: {err}", file=sys.stderr)
        _die(f"{len(meta_errors)} invalid metadata_json value(s) found")

    generic_errors = _validate_generic_rows(media_items, media_assets, animations)
    if generic_errors:
        for err in generic_errors:
            print(f"  INVALID: {err}", file=sys.stderr)
        _die(f"{len(generic_errors)} non-portable source value(s) found")

    clean_reels, dirty_reels = _classify_reels(reels)
    if dirty_reels and not args.skip_legacy_reels:
        print("  ERROR: Dirty legacy reels rows found:", file=sys.stderr)
        for dr in dirty_reels:
            desc = "; ".join(dr["issues"])
            print(f"    id={dr['id']!r}: {desc}", file=sys.stderr)
        _die(
            "Dirty reels rows require explicit policy. "
            "Use --skip-legacy-reels to skip the reels table entirely.",
        )
    if args.skip_legacy_reels:
        _echo("  Legacy reels: skipped (--skip-legacy-reels)")
        clean_reels = []

    # ── 6. Setup PostgreSQL and optional upgrade ──
    pg = create_engine(postgres_url, pool_pre_ping=True)
    if args.upgrade_schema:
        _upgrade_target(postgres_url)
    _verify_alembic_head(pg, "target")

    # ── 7. Target preflight ──
    _target_preflight(pg, args.reset_target)

    # ── 8. Transfer and validate in one transaction ──
    _echo("")
    _echo("Transferring data...")
    source_data: dict[str, list[dict[str, Any]]] = {
        "media_items": media_items,
        "media_assets": media_assets,
        "judgmental_animations": animations,
        "reels": clean_reels,
    }

    with pg.begin() as conn:
        if args.reset_target:
            _reset_target(conn)
            _echo("  Target reset — application tables truncated")
        _insert_rows(conn, "media_items", MEDIA_ITEM_COLUMNS, media_items)
        _insert_rows(conn, "media_assets", MEDIA_ASSET_COLUMNS, media_assets)
        _insert_rows(conn, "judgmental_animations", ANIMATION_COLUMNS, animations)
        if clean_reels:
            _insert_rows(conn, "reels", REQUIRED_REELS_COLUMNS, clean_reels)
        _echo(f"  Inserted {len(media_items)} media_items")
        _echo(f"  Inserted {len(media_assets)} media_assets")
        _echo(f"  Inserted {len(animations)} judgmental_animations")
        if clean_reels:
            _echo(f"  Inserted {len(clean_reels)} reels")
        _verify_transfer(conn, source_data)

    _echo("")
    _echo("Post-commit sequence repair...")
    for action in _repair_sequences(pg):
        _echo(action)
    if args.verify:
        _echo("  Sequence probes...")
        for probe in _probe_sequences(pg):
            _echo(probe)

    # ── 11. Report missing files ──
    missing = _report_missing_files(media_assets, os.environ.get("OUTPUT_DIR", ""))
    if missing:
        _echo(f"  Missing files ({len(missing)}):")
        for path in missing[:10]:
            _echo(f"    {path}")
        if len(missing) > 10:
            _echo(f"    ... and {len(missing) - 10} more")
    else:
        _echo("  No missing files")

    _echo("")
    _echo("Transfer complete.")


if __name__ == "__main__":
    main()
