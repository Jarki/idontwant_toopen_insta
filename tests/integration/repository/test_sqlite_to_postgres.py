"""Tests for the SQLite-to-PostgreSQL transfer script.

Unit-level tests cover validation, policy, and error paths without needing
PostgreSQL. Integration tests requiring a live PostgreSQL target are guarded
by pg_url() and skip cleanly when no database is reachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from docker.scripts import sqlite_to_postgres
from ig_reel_downloader.repository.schema import Base

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "docker" / "scripts"
TRANSFER_SCRIPT = SCRIPTS_DIR / "sqlite_to_postgres.py"
LATEST_REVISION = "20260715_0004"

# Column tuples must match the script.
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
REELS_COLUMNS = (
    "id",
    "title",
    "description",
    "filepath",
    "url",
    "like_count",
    "created_at",
    "comments",
)


def script_path() -> Path:
    assert TRANSFER_SCRIPT.is_file(), f"Transfer script not found: {TRANSFER_SCRIPT}"
    return TRANSFER_SCRIPT


def run_script(*args: str, expect_fail: bool = False) -> str:
    """Run the transfer script and return stdout+stderr.

    Raises AssertionError on unexpected exit code.
    """
    cmd = [sys.executable, str(TRANSFER_SCRIPT), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr
    if expect_fail:
        if result.returncode == 0:
            raise AssertionError(
                f"Expected non-zero exit but got 0.\nOutput:\n{output}",
            )
    else:
        if result.returncode != 0:
            raise AssertionError(
                f"Expected exit code 0 but got {result.returncode}.\n"
                f"Stderr:\n{result.stderr}\nStdout:\n{result.stdout}",
            )
    return output


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def create_sqlite_db(
    path: Path,
    *,
    alembic_head: str = LATEST_REVISION,
    rows: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """Create a minimal test SQLite database with schema matching production.

    Creates application tables via direct SQLAlchemy schema creation, inserts
    fixture rows, and sets the Alembic version to *alembic_head*.
    """
    from sqlalchemy import inspect as sa_insp

    engine = create_engine(f"sqlite:///{path}")

    # Build the schema from neutral records.
    Base.metadata.create_all(engine)
    inspector = sa_insp(engine)
    existing = set(inspector.get_table_names())

    # Create reels table (no ORM record for it in schema.py).
    if "reels" not in existing:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE reels ("
                    "id VARCHAR NOT NULL PRIMARY KEY, "
                    "title VARCHAR NOT NULL, "
                    "description VARCHAR, "
                    "filepath VARCHAR NOT NULL, "
                    "url VARCHAR NOT NULL, "
                    "like_count INTEGER NOT NULL, "
                    "created_at DATETIME NOT NULL, "
                    "comments VARCHAR NOT NULL)"
                )
            )
        existing.add("reels")

    # Create alembic_version table (not in Base.metadata).
    if "alembic_version" not in existing:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
        existing.add("alembic_version")

    # Clear any migration-inserted data from application tables.
    app_tables = {"media_assets", "media_items", "judgmental_animations", "reels"}
    present = [t for t in app_tables if t in existing]
    if present:
        with engine.begin() as conn:
            for table in present:
                conn.execute(text(f"DELETE FROM {table}"))

    # Insert fixture rows.
    rows = rows or {}
    with engine.begin() as conn:
        for table, column_names in [
            ("media_items", MEDIA_ITEM_COLUMNS),
            ("media_assets", MEDIA_ASSET_COLUMNS),
            ("judgmental_animations", ANIMATION_COLUMNS),
            ("reels", REELS_COLUMNS),
        ]:
            if table in present and table in rows:
                col_list = ", ".join(column_names)
                placeholders = ", ".join(f":{c}" for c in column_names)
                for row in rows[table]:
                    conn.execute(
                        text(
                            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
                        ),
                        row,
                    )
    # Set Alembic version.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": alembic_head},
        )

    engine.dispose()


def minimal_media_item(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a canonical media_items row dict."""
    row = {
        "id": "instagram:reel:test001",
        "provider": "instagram",
        "media_kind": "reel",
        "provider_item_id": "test001",
        "original_url": "https://www.instagram.com/reel/test001",
        "title": "Test Reel",
        "description": "A test",
        "metadata_json": json.dumps({"like_count": 42, "comments": []}),
        "created_at": "2026-07-15 12:00:00",
        "updated_at": "2026-07-15 12:00:00",
    }
    if override:
        row.update(override)
    return row


def minimal_asset(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a canonical media_assets row dict."""
    row = {
        "id": 1,
        "media_item_id": "instagram:reel:test001",
        "asset_index": 0,
        "asset_type": "video",
        "filepath": "output/test001-0.mp4",
        "mime_type": "video/mp4",
        "width": 1920,
        "height": 1080,
        "duration_seconds": 30.5,
        "file_size_bytes": 1048576,
        "created_at": "2026-07-15 12:00:00",
    }
    if override:
        row.update(override)
    return row


def minimal_animation(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a canonical judgmental_animations row dict."""
    row = {
        "id": 1,
        "file_id": "file-id-1",
        "file_unique_id": "unique-id-1",
        "created_at": "2026-07-15 12:00:00",
        "updated_at": "2026-07-15 12:00:00",
    }
    if override:
        row.update(override)
    return row


def minimal_reel(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a canonical reels row dict."""
    row = {
        "id": "reel-001",
        "title": "Legacy Reel",
        "description": "A legacy reel",
        "filepath": "output/reel-001.mp4",
        "url": "https://www.instagram.com/reel/reel-001",
        "like_count": 42,
        "created_at": "2026-07-15 12:00:00",
        "comments": "[]",
    }
    if override:
        row.update(override)
    return row


# ---------------------------------------------------------------------------
# PostgreSQL connection for integration tests
# ---------------------------------------------------------------------------


def pg_url() -> str | None:
    """Return a PostgreSQL URL for integration tests, or None.

    Checks PGTEST_URL env var first, then tries localhost with test creds.
    """
    url = os.environ.get("PGTEST_URL")
    if url:
        return url

    # Default test credentials for local Docker Compose.
    for candidate in (
        "postgresql+psycopg://postgres:postgres@localhost:5432/ig_reel_downloader_test",
        "postgresql+psycopg://app:app@localhost:5432/ig_reel_downloader_test",
    ):
        try:
            engine = create_engine(candidate, connect_args={"connect_timeout": 2})
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            return candidate
        except Exception:
            continue
    return None


# ===================================================================
# Unit tests (no PostgreSQL required)
# ===================================================================


class TestArgParsing:
    """CLI argument handling and validation."""

    def test_requires_sqlite_path(self) -> None:
        """Missing --sqlite-path should exit non-zero."""
        output = run_script(
            "--postgres-url", "postgresql+psycopg://h:5432/db", expect_fail=True
        )
        assert "the following arguments are required: --sqlite-path" in output

    def test_rejects_no_target_url(self) -> None:
        """No --postgres-url and no DATABASE_URL should exit."""
        with patch.dict(os.environ, {}, clear=True):
            output = run_script("--sqlite-path", "/nonexistent/db.db", expect_fail=True)
            assert "No target URL provided" in output

    def test_rejects_non_psycopg_url(self) -> None:
        """Non-psycopg dialect must be rejected."""
        for bad_url in (
            "postgresql://h:5432/db",
            "postgresql+psycopg2://h:5432/db",
            "sqlite:///data.db",
            "mysql://h:3306/db",
        ):
            output = run_script(
                "--sqlite-path",
                "/nonexistent/db.db",
                "--postgres-url",
                bad_url,
                expect_fail=True,
            )
            assert "postgresql+psycopg" in output, f"Failed to reject: {bad_url}"

    def test_accepts_psycopg_url(self) -> None:
        """Valid psycopg URL should pass URL validation (fails later on missing source)."""
        output = run_script(
            "--sqlite-path",
            "/nonexistent/db.db",
            "--postgres-url",
            "postgresql+psycopg://user:pass@h:5432/db",
            expect_fail=True,
        )
        # Should fail on missing SQLite source, not URL validation.
        assert "SQLite source not found" in output
        assert "postgresql+psycopg" not in output  # no URL complaint


class TestSourcePreflight:
    """Source database preflight validation."""

    def test_missing_source_file_exits(self, tmp_path: Path) -> None:
        """Non-existent SQLite file should exit cleanly."""
        missing = tmp_path / "nope.db"
        output = run_script(
            "--sqlite-path",
            str(missing),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "SQLite source not found" in output

    def test_wrong_alembic_head_exits(self, tmp_path: Path) -> None:
        """Wrong Alembic version should produce a clear error."""
        db = tmp_path / "test.db"
        create_sqlite_db(db, alembic_head="00000000_0000")

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "Alembic version" in output
        assert "00000000_0000" in output
        assert LATEST_REVISION in output

    def test_previous_compatible_alembic_revision_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """A source one migration behind the target remains transferable."""
        db = tmp_path / "test.db"
        create_sqlite_db(db, alembic_head="20260710_0003")
        engine = create_engine(f"sqlite:///{db}")
        sqlite_to_postgres._verify_alembic_head(
            engine,
            "source",
            allowed_versions=sqlite_to_postgres.SUPPORTED_SOURCE_ALEMBIC_VERSIONS,
        )
        engine.dispose()

    def test_missing_required_table_exits(self, tmp_path: Path) -> None:
        """Missing a required application table should exit."""
        db = tmp_path / "test.db"
        # Create a valid Alembic environment but without media_items.
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        # Create alembic_version table (not included in Base.metadata).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY"
                    ")"
                ),
            )
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": LATEST_REVISION},
            )
            # Drop one of the required tables.
            conn.execute(text("DROP TABLE IF EXISTS media_items"))
        engine.dispose()

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "missing required tables" in output
        assert "media_items" in output

    def test_missing_column_exits(self, tmp_path: Path) -> None:
        """Missing a required column should exit."""
        db = tmp_path / "test.db"
        create_sqlite_db(db)
        # Drop a column from media_assets.
        engine = create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE media_assets_v2 AS SELECT "
                    "id, media_item_id, asset_index, asset_type, filepath, "
                    "mime_type, width, height, duration_seconds, "
                    "file_size_bytes FROM media_assets"
                ),
            )
            conn.execute(text("DROP TABLE media_assets"))
            conn.execute(text("ALTER TABLE media_assets_v2 RENAME TO media_assets"))
        engine.dispose()

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "missing required columns" in output
        assert "created_at" in output


class TestAssetTypeValidation:
    """Asset type validation."""

    def test_invalid_asset_type_exits(self, tmp_path: Path) -> None:
        """An asset with invalid type must fail before transfer."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset({"asset_type": "audio"})],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "invalid asset type" in output.lower() or "invalid asset_type" in output


class TestMetadataValidation:
    """Metadata JSON validation."""

    def test_invalid_metadata_json_exits(self, tmp_path: Path) -> None:
        """Invalid JSON in metadata_json must fail."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item({"metadata_json": "{bad json}"})],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "not valid JSON" in output or "metadata_json" in output.lower()

    def test_non_object_metadata_exits(self, tmp_path: Path) -> None:
        """metadata_json that parses but isn't a dict must fail."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [
                    minimal_media_item({"metadata_json": '"just a string"'})
                ],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "not a JSON object" in output

    def test_empty_string_metadata_fails(self, tmp_path: Path) -> None:
        """Empty metadata_json string should fail validation."""
        db = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db}")
        Base.metadata.create_all(engine)
        # Create alembic_version table.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": LATEST_REVISION},
            )
            conn.execute(
                text(
                    "INSERT INTO media_items ("
                    "id, provider, media_kind, provider_item_id, original_url, "
                    "title, description, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    ":id, :prov, :mk, :pid, :url, :title, :desc, '', :ca, :ua)"
                ),
                {
                    "id": "instagram:reel:test001",
                    "prov": "instagram",
                    "mk": "reel",
                    "pid": "test001",
                    "url": "https://ig.com/reel/test001",
                    "title": "test",
                    "desc": None,
                    "ca": "2026-07-15 12:00:00",
                    "ua": "2026-07-15 12:00:00",
                },
            )
        engine.dispose()

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "metadata_json" in output.lower()


class TestPortableValueValidation:
    """PostgreSQL type and relationship preflight."""

    def test_negative_file_size_fails_before_target_connection(
        self,
        tmp_path: Path,
    ) -> None:
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset({"file_size_bytes": -1})],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )

        assert "invalid file_size_bytes" in output

    def test_invalid_timestamp_fails_before_target_connection(
        self,
        tmp_path: Path,
    ) -> None:
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item({"updated_at": "not-a-timestamp"})],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )

        assert "invalid updated_at" in output


class TestLegacyReelsPolicy:
    """Legacy reels policy (dirty row handling)."""

    def test_clean_reels_pass(self, tmp_path: Path) -> None:
        """Clean reels rows should not block the transfer."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
                "reels": [minimal_reel()],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        # Should fail on target connection (no PG), but not on reels.
        assert "dirty" not in output.lower()

    def _insert_dirty_reel(
        self, db: Path, overrides: dict[str, Any] | None = None
    ) -> None:
        """Insert a reels row with relaxed constraints (no NOT NULL)."""
        import sqlite3

        conn = sqlite3.connect(str(db))
        # Drop and recreate reels table without NOT NULL constraints so we
        # can insert dirty rows that real-world legacy databases may have.
        conn.execute("DROP TABLE IF EXISTS reels")
        conn.execute(
            "CREATE TABLE reels ("
            "id VARCHAR, title VARCHAR, description VARCHAR, "
            "filepath VARCHAR, url VARCHAR, "
            "like_count INTEGER, created_at DATETIME, comments VARCHAR)"
        )
        row = minimal_reel(overrides)
        conn.execute(
            "INSERT INTO reels (id, title, description, filepath, url, "
            "like_count, created_at, comments) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("id"),
                row.get("title"),
                row.get("description"),
                row.get("filepath"),
                row.get("url"),
                row.get("like_count"),
                row.get("created_at"),
                row.get("comments"),
            ),
        )
        conn.commit()
        conn.close()

    def test_dirty_reels_exit_by_default(self, tmp_path: Path) -> None:
        """Dirty reels rows should cause exit unless --skip-legacy-reels."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
            },
        )
        self._insert_dirty_reel(db, {"url": None, "like_count": None})

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            expect_fail=True,
        )
        assert "dirty" in output.lower() or "skip-legacy-reels" in output
        assert "url is NULL" in output
        assert "like_count is NULL" in output

    def test_skip_legacy_reels_allows_dirty(self, tmp_path: Path) -> None:
        """--skip-legacy-reels should skip reels and proceed."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
            },
        )
        self._insert_dirty_reel(db, {"url": None})

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            "--skip-legacy-reels",
            expect_fail=True,
        )
        # Should proceed past reels check, fail on PG connection.
        assert "skipped" in output.lower()
        assert "dirty" not in output.lower()

    def test_skip_legacy_reels_with_no_reels(self, tmp_path: Path) -> None:
        """--skip-legacy-reels with no reels table should not fail."""
        db = tmp_path / "test.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
            },
        )

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            "postgresql+psycopg://u:p@h:5432/d",
            "--skip-legacy-reels",
            expect_fail=True,
        )
        # Should proceed past reels check, fail on PG connection.
        assert (
            "Transferring" not in output
            or "Connection refused" in output
            or "could not translate host name" in output
        )


class TestTargetPreflight:
    """Target database preflight logic (tested by calling the module directly)."""

    def test_target_nonempty_without_reset_exits(self, tmp_path: Path) -> None:
        """A target with existing rows rejects without --reset-target."""
        target = tmp_path / "target.db"
        create_sqlite_db(
            target,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
            },
        )
        engine = create_engine(f"sqlite:///{target}")

        with pytest.raises(SystemExit):
            sqlite_to_postgres._target_preflight(engine, reset=False)

        engine.dispose()


class TestMissingFileReporting:
    def test_nested_output_dir_does_not_duplicate_path_prefix(
        self,
        tmp_path: Path,
    ) -> None:
        output_dir = tmp_path / "media" / "archive"
        existing = output_dir / "provider" / "existing.mp4"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"fixture")

        missing = sqlite_to_postgres._report_missing_files(
            [
                {"filepath": "media/archive/provider/existing.mp4"},
                {"filepath": "media/archive/provider/missing.mp4"},
            ],
            str(output_dir),
        )

        assert missing == [str(output_dir / "provider" / "missing.mp4")]


# ===================================================================
# Integration tests (require PostgreSQL)
# ===================================================================

pytestmark_integration = pytest.mark.skipif(
    pg_url() is None,
    reason="PostgreSQL not available (set PGTEST_URL or start a local instance)",
)


class TestPostgresTransfer:
    """Integration tests requiring a live PostgreSQL target."""

    @pytest.fixture(autouse=True)
    def _ensure_pg(self) -> None:
        if pg_url() is None:
            pytest.skip("PostgreSQL not available")

    @pytest.fixture
    def target_db(self) -> str:
        url = pg_url()
        assert url is not None
        # Drop and recreate the test schema.
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        return url

    def _run_on_pg(
        self,
        tmp_path: Path,
        target_url: str,
        *extra_args: str,
        rows: dict[str, list[dict[str, Any]]] | None = None,
        expect_fail: bool = False,
    ) -> str:
        """Create a SQLite db and run the transfer against a live PG target."""
        db = tmp_path / "source.db"
        create_sqlite_db(
            db,
            rows=rows
            or {
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
                "judgmental_animations": [minimal_animation()],
            },
        )

        return run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            target_url,
            "--upgrade-schema",
            *extra_args,
            expect_fail=expect_fail,
        )

    @staticmethod
    def _query_scalar(url: str, statement: str) -> Any:
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                return conn.execute(text(statement)).scalar()
        finally:
            engine.dispose()

    def _count_rows(self, url: str, table: str) -> int:
        return int(self._query_scalar(url, f"SELECT COUNT(*) FROM {table}") or 0)

    def _table_exists(self, url: str, table: str) -> bool:
        engine = create_engine(url)
        try:
            return table in inspect(engine).get_table_names()
        finally:
            engine.dispose()

    def test_successful_transfer(self, tmp_path: Path, target_db: str) -> None:
        """Basic happy-path transfer: all rows copied, counts match."""
        output = self._run_on_pg(tmp_path, target_db)
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "media_items") == 1
        assert self._count_rows(target_db, "media_assets") == 1
        assert self._count_rows(target_db, "judgmental_animations") == 1

    def test_historical_migrations_upgrade_legacy_postgres_fixture(
        self,
        target_db: str,
    ) -> None:
        engine = create_engine(target_db)
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE reels (
                        id VARCHAR PRIMARY KEY,
                        title VARCHAR NOT NULL,
                        description VARCHAR,
                        filepath VARCHAR NOT NULL,
                        url VARCHAR NOT NULL,
                        like_count INTEGER NOT NULL,
                        created_at TIMESTAMP NOT NULL,
                        comments VARCHAR NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO reels (
                        id, title, description, filepath, url,
                        like_count, created_at, comments
                    ) VALUES (
                        'legacy-pg', 'Legacy PG', NULL, 'output/legacy.mp4',
                        'https://www.instagram.com/reel/legacy-pg',
                        12, '2026-07-15 12:00:00', '[]'
                    )
                    """
                )
            )
        engine.dispose()

        config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
        config.attributes["database_url"] = target_db
        command.upgrade(config, "head")

        assert self._count_rows(target_db, "reels") == 1
        assert self._count_rows(target_db, "media_items") == 1
        assert self._count_rows(target_db, "media_assets") == 1

    def test_row_parity(self, tmp_path: Path, target_db: str) -> None:
        """Multiple rows and assets transfer correctly."""
        rows = {
            "media_items": [
                minimal_media_item(
                    {
                        "id": "instagram:reel:a",
                        "provider_item_id": "a",
                        "original_url": "https://ig.com/reel/a",
                        "metadata_json": "{}",
                    }
                ),
                minimal_media_item(
                    {
                        "id": "instagram:reel:b",
                        "provider_item_id": "b",
                        "original_url": "https://ig.com/reel/b",
                        "metadata_json": "{}",
                    }
                ),
            ],
            "media_assets": [
                minimal_asset(
                    {
                        "id": 1,
                        "media_item_id": "instagram:reel:a",
                        "asset_index": 0,
                        "filepath": "a-0.mp4",
                    }
                ),
                minimal_asset(
                    {
                        "id": 2,
                        "media_item_id": "instagram:reel:a",
                        "asset_index": 1,
                        "filepath": "a-1.mp4",
                    }
                ),
                minimal_asset(
                    {
                        "id": 3,
                        "media_item_id": "instagram:reel:b",
                        "asset_index": 0,
                        "filepath": "b-0.mp4",
                    }
                ),
            ],
            "judgmental_animations": [
                minimal_animation({"id": 1, "file_id": "f1", "file_unique_id": "u1"}),
                minimal_animation({"id": 2, "file_id": "f2", "file_unique_id": "u2"}),
            ],
        }
        output = self._run_on_pg(tmp_path, target_db, rows=rows)
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "media_items") == 2
        assert self._count_rows(target_db, "media_assets") == 3
        assert self._count_rows(target_db, "judgmental_animations") == 2

    def test_legacy_reels_transferred(self, tmp_path: Path, target_db: str) -> None:
        """Clean reels rows are transferred with the rest."""
        rows = {
            "media_items": [minimal_media_item()],
            "media_assets": [minimal_asset()],
            "reels": [minimal_reel()],
        }
        output = self._run_on_pg(tmp_path, target_db, rows=rows)
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "reels") == 1

    def test_skip_legacy_reels_on_target(self, tmp_path: Path, target_db: str) -> None:
        """--skip-legacy-reels skips reels even when they're clean."""
        rows = {
            "media_items": [minimal_media_item()],
            "media_assets": [minimal_asset()],
            "reels": [minimal_reel()],
        }
        output = self._run_on_pg(tmp_path, target_db, "--skip-legacy-reels", rows=rows)
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "reels") == 0

    def test_target_nonempty_refused(self, tmp_path: Path, target_db: str) -> None:
        """Pre-existing rows in application tables should be refused."""
        # First transfer succeeds.
        self._run_on_pg(tmp_path, target_db)

        # Second transfer should fail.
        db2 = tmp_path / "source2.db"
        create_sqlite_db(
            db2,
            rows={
                "media_items": [minimal_media_item({"id": "instagram:reel:other"})],
                "media_assets": [
                    minimal_asset({"id": 10, "media_item_id": "instagram:reel:other"})
                ],
            },
        )
        output = run_script(
            "--sqlite-path",
            str(db2),
            "--postgres-url",
            target_db,
            "--upgrade-schema",
            expect_fail=True,
        )
        assert "existing application rows" in output or "existing" in output.lower()

    def test_reset_target_clears_and_transfers(
        self, tmp_path: Path, target_db: str
    ) -> None:
        """--reset-target should clear existing data and proceed."""
        # First transfer.
        self._run_on_pg(tmp_path, target_db)
        assert self._count_rows(target_db, "media_items") == 1

        # Second transfer with --reset-target.
        db2 = tmp_path / "source2.db"
        create_sqlite_db(
            db2,
            rows={
                "media_items": [minimal_media_item({"id": "instagram:reel:new"})],
                "media_assets": [
                    minimal_asset({"id": 5, "media_item_id": "instagram:reel:new"})
                ],
            },
        )
        output = run_script(
            "--sqlite-path",
            str(db2),
            "--postgres-url",
            target_db,
            "--upgrade-schema",
            "--reset-target",
            expect_fail=False,
        )
        assert "Transfer complete" in output
        # Old data is gone, new data present.
        assert self._count_rows(target_db, "media_items") == 1
        engine = create_engine(target_db)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM media_items"),
            ).fetchone()
            assert row is not None
            assert row[0] == "instagram:reel:new"
        engine.dispose()

    def test_failure_rollback_leaves_target_unchanged(
        self,
        tmp_path: Path,
        target_db: str,
    ) -> None:
        """A transactional reset is rolled back when an insert fails."""
        self._run_on_pg(tmp_path, target_db)
        assert self._count_rows(target_db, "media_items") == 1
        assert self._count_rows(target_db, "media_assets") == 1

        engine = create_engine(target_db)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE media_assets ADD CONSTRAINT reject_image_fixture "
                    "CHECK (asset_type <> 'image')"
                )
            )
        engine.dispose()

        failing_source = tmp_path / "failing-source.db"
        create_sqlite_db(
            failing_source,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset({"asset_type": "image"})],
            },
        )
        output = run_script(
            "--sqlite-path",
            str(failing_source),
            "--postgres-url",
            target_db,
            "--upgrade-schema",
            "--reset-target",
            expect_fail=True,
        )
        assert "reject_image_fixture" in output

        assert self._count_rows(target_db, "media_items") == 1
        assert self._count_rows(target_db, "media_assets") == 1

    def test_sequence_probes_avoid_valid_source_keys(
        self,
        tmp_path: Path,
        target_db: str,
    ) -> None:
        probe_id = "__probe__"
        output = self._run_on_pg(
            tmp_path,
            target_db,
            "--verify",
            rows={
                "media_items": [
                    minimal_media_item(
                        {
                            "id": probe_id,
                            "provider": "probe",
                            "media_kind": "probe",
                            "provider_item_id": probe_id,
                        }
                    )
                ],
                "media_assets": [minimal_asset({"media_item_id": probe_id})],
                "judgmental_animations": [
                    minimal_animation({"file_id": probe_id, "file_unique_id": probe_id})
                ],
            },
        )

        assert "Transfer complete" in output

    def test_sequence_repair(self, tmp_path: Path, target_db: str) -> None:
        """After transfer, autoincrement sequences accept new rows."""
        self._run_on_pg(tmp_path, target_db)

        engine = create_engine(target_db)
        with engine.begin() as conn:
            # Insert a new animation without specifying ID.
            conn.execute(
                text(
                    "INSERT INTO judgmental_animations "
                    "(file_id, file_unique_id, created_at, updated_at) "
                    "VALUES (:f, :fu, NOW(), NOW())",
                ),
                {"f": "new-file", "fu": "new-uid"},
            )
            # Insert a new asset.
            conn.execute(
                text(
                    "INSERT INTO media_assets "
                    "(media_item_id, asset_index, asset_type, filepath, created_at) "
                    "VALUES (:mi, 1, 'video', '/dev/null', NOW())",
                ),
                {"mi": "instagram:reel:test001"},
            )
        engine.dispose()

        assert self._count_rows(target_db, "judgmental_animations") == 2
        assert self._count_rows(target_db, "media_assets") == 2

    def test_empty_table_transfer(self, tmp_path: Path, target_db: str) -> None:
        """Empty application tables should transfer without error."""
        db = tmp_path / "source.db"
        create_sqlite_db(db)

        output = run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            target_db,
            "--upgrade-schema",
            expect_fail=False,
        )
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "media_items") == 0
        assert self._count_rows(target_db, "media_assets") == 0
        assert self._count_rows(target_db, "judgmental_animations") == 0

    def test_verify_flag_probes_sequences(self, tmp_path: Path, target_db: str) -> None:
        """--verify runs sequence probes and reports them."""
        output = self._run_on_pg(tmp_path, target_db, "--verify")
        assert "sequence probe" in output.lower() or "probe" in output.lower()
        assert "Transfer complete" in output

    def test_source_sqlite_unchanged(self, tmp_path: Path, target_db: str) -> None:
        """The SQLite source file must not be modified by the transfer."""
        db = tmp_path / "source.db"
        create_sqlite_db(
            db,
            rows={
                "media_items": [minimal_media_item()],
                "media_assets": [minimal_asset()],
            },
        )
        original_mtime = db.stat().st_mtime_ns

        run_script(
            "--sqlite-path",
            str(db),
            "--postgres-url",
            target_db,
            "--upgrade-schema",
        )

        # mtime should be unchanged (read-only mode).
        assert db.stat().st_mtime_ns == original_mtime

    def test_transfer_with_reels_and_skip(self, tmp_path: Path, target_db: str) -> None:
        """--skip-legacy-reels with clean reels transfers only app tables."""
        rows = {
            "media_items": [minimal_media_item()],
            "media_assets": [minimal_asset()],
            "judgmental_animations": [minimal_animation()],
            "reels": [minimal_reel(), minimal_reel({"id": "reel-002"})],
        }
        output = self._run_on_pg(tmp_path, target_db, "--skip-legacy-reels", rows=rows)
        assert "Transfer complete" in output
        assert self._count_rows(target_db, "reels") == 0
        assert self._count_rows(target_db, "media_items") == 1
        assert self._count_rows(target_db, "media_assets") == 1
