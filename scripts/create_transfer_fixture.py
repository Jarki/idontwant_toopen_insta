#!/usr/bin/env python3
"""Create a representative SQLite source for the Compose transfer test."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--invalid-asset-type", action="store_true")
    parser.add_argument("--invalid-metadata-shape", action="store_true")
    parser.add_argument("--dirty-legacy", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    sqlite_path: Path = args.sqlite_path
    output_dir: Path = args.output_dir
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{sqlite_path}"
    command.upgrade(config, "head")

    timestamp = "2026-07-15 12:00:00"
    with sqlite3.connect(sqlite_path) as connection:
        if args.dirty_legacy:
            connection.execute("DROP TABLE reels")
            connection.execute(
                """
                CREATE TABLE reels (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    filepath TEXT,
                    url TEXT,
                    like_count INTEGER,
                    created_at DATETIME,
                    comments TEXT
                )
                """
            )
        connection.execute(
            """
            INSERT INTO media_items (
                id, provider, media_kind, provider_item_id, original_url, title,
                description, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "instagram:post:fixture",
                "instagram",
                "post",
                "fixture",
                "https://www.instagram.com/p/fixture",
                "Fixture carousel",
                "Representative transfer fixture",
                "[]"
                if args.invalid_metadata_shape
                else '{"comments": [], "like_count": 42}',
                timestamp,
                timestamp,
            ),
        )
        connection.executemany(
            """
            INSERT INTO media_assets (
                id, media_item_id, asset_index, asset_type, filepath, mime_type,
                width, height, duration_seconds, file_size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    8,
                    "instagram:post:fixture",
                    1,
                    "audio" if args.invalid_asset_type else "image",
                    "output/missing.jpg",
                    "image/jpeg",
                    1080,
                    1350,
                    None,
                    5_000_000_000,
                    timestamp,
                ),
                (
                    7,
                    "instagram:post:fixture",
                    0,
                    "video",
                    "output/existing.mp4",
                    "video/mp4",
                    1080,
                    1920,
                    15.5,
                    1024,
                    timestamp,
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO judgmental_animations (
                id, file_id, file_unique_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (5, "file-id-a", "unique-id-a", timestamp, timestamp),
                (9, "file-id-b", "unique-id-b", timestamp, timestamp),
            ],
        )
        connection.execute(
            """
            INSERT INTO reels (
                id, title, description, filepath, url, like_count, created_at, comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-fixture",
                None if args.dirty_legacy else "Legacy fixture",
                "Valid legacy row",
                "output/existing.mp4",
                "https://www.instagram.com/reel/legacy-fixture",
                7,
                "not-a-timestamp" if args.dirty_legacy else timestamp,
                "not-json" if args.dirty_legacy else "[]",
            ),
        )
        connection.commit()

    (output_dir / "existing.mp4").write_bytes(b"fixture-media")


if __name__ == "__main__":
    main()
