"""create generic media tables

Revision ID: 20260707_0002
Revises: 20260707_0001
Create Date: 2026-07-07 00:00:00.000000
"""

import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20260707_0002"
down_revision: str | None = "20260707_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {"media_items", "media_assets"} & set(inspector.get_table_names())
    if existing:
        names = ", ".join(sorted(existing))
        msg = f"Unexpected pre-existing generic media table(s): {names}"
        raise RuntimeError(msg)

    op.create_table(
        "media_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("media_kind", sa.String(), nullable=False),
        sa.Column("provider_item_id", sa.String(), nullable=False),
        sa.Column("original_url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "media_kind",
            "provider_item_id",
            name="uq_media_items_provider_kind_item",
        ),
    )
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("media_item_id", sa.String(), nullable=False),
        sa.Column("asset_index", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("filepath", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "media_item_id",
            "asset_index",
            name="uq_media_assets_item_index",
        ),
    )
    _copy_legacy_reels(bind)


def downgrade() -> None:
    op.drop_table("media_assets")
    op.drop_table("media_items")


def _copy_legacy_reels(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if "reels" not in inspector.get_table_names():
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, title, description, filepath, url, like_count, "
            "created_at, comments FROM reels"
        )
    ).mappings()
    for row in rows:
        reel_id = str(row["id"])
        created_at = _parse_datetime(row["created_at"])
        created_at_value = created_at.isoformat(sep=" ")
        metadata = _metadata(row["like_count"], row["comments"])
        media_item_id = f"instagram:reel:{reel_id}"
        bind.execute(
            sa.text(
                """
INSERT INTO media_items (
    id, provider, media_kind, provider_item_id, original_url, title, description,
    metadata_json, created_at, updated_at
)
VALUES (
    :id, 'instagram', 'reel', :provider_item_id, :original_url, :title,
    :description, :metadata_json, :created_at, :updated_at
)
ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": media_item_id,
                "provider_item_id": reel_id,
                "original_url": row["url"]
                or f"https://www.instagram.com/reel/{reel_id}",
                "title": row["title"] or "",
                "description": row["description"],
                "metadata_json": json.dumps(metadata),
                "created_at": created_at_value,
                "updated_at": created_at_value,
            },
        )
        filepath = row["filepath"]
        if filepath:
            bind.execute(
                sa.text(
                    """
INSERT INTO media_assets (media_item_id, asset_index, asset_type, filepath, created_at)
VALUES (:media_item_id, 0, 'video', :filepath, :created_at)
ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "media_item_id": media_item_id,
                    "filepath": filepath,
                    "created_at": created_at_value,
                },
            )


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()
    return datetime.now()


def _metadata(like_count: object, comments: object) -> dict[str, object]:
    metadata: dict[str, object] = {"like_count": _safe_int(like_count), "comments": []}
    if isinstance(comments, str) and comments:
        try:
            parsed = json.loads(comments)
            metadata["comments"] = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            metadata["comments_raw"] = comments
    return metadata


def _safe_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
