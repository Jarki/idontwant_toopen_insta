"""create observability tables

Revision ID: 20260716_0005
Revises: 20260715_0004
Create Date: 2026-07-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0005"
down_revision: str | None = "20260715_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("language_code", sa.String(), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "media_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("normalized_url", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("media_kind", sa.String(), nullable=False),
        sa.Column("provider_item_id", sa.String(), nullable=True),
        sa.Column("media_item_id", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("failure_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["telegram_users.id"]),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_items.id"]),
        sa.CheckConstraint(
            "(media_item_id IS NULL AND failure_reason IS NULL "
            "AND failure_url IS NULL AND completed_at IS NULL) OR "
            "(media_item_id IS NOT NULL AND failure_reason IS NULL "
            "AND failure_url IS NULL AND completed_at IS NOT NULL) OR "
            "(media_item_id IS NULL AND failure_reason IS NOT NULL "
            "AND failure_url IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_media_requests_valid_outcome",
        ),
    )
    op.create_index(
        "ix_media_requests_user_created_at",
        "media_requests",
        ["telegram_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_requests_user_created_at",
        table_name="media_requests",
    )
    op.drop_table("media_requests")
    op.drop_table("telegram_users")
