"""add chat and delivery tracking to media requests

Revision ID: 20260718_0007
Revises: 20260717_0006
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0007"
down_revision: str | None = "20260717_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_requests",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "media_requests",
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
    )
    op.drop_constraint(
        "ck_media_requests_valid_outcome",
        "media_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_media_requests_valid_outcome",
        "media_requests",
        "(media_item_id IS NULL AND failure_reason IS NULL "
        "AND failure_url IS NULL AND completed_at IS NULL "
        "AND delivered_at IS NULL) OR "
        "(media_item_id IS NOT NULL AND failure_reason IS NULL "
        "AND failure_url IS NULL AND completed_at IS NOT NULL) OR "
        "(media_item_id IS NULL AND failure_reason IS NOT NULL "
        "AND failure_url IS NOT NULL AND completed_at IS NOT NULL "
        "AND delivered_at IS NULL)",
    )
    op.create_index(
        "ix_media_requests_chat_user",
        "media_requests",
        ["telegram_chat_id", "telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_requests_chat_user",
        table_name="media_requests",
    )
    op.drop_constraint(
        "ck_media_requests_valid_outcome",
        "media_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_media_requests_valid_outcome",
        "media_requests",
        "(media_item_id IS NULL AND failure_reason IS NULL "
        "AND failure_url IS NULL AND completed_at IS NULL) OR "
        "(media_item_id IS NOT NULL AND failure_reason IS NULL "
        "AND failure_url IS NULL AND completed_at IS NOT NULL) OR "
        "(media_item_id IS NULL AND failure_reason IS NOT NULL "
        "AND failure_url IS NOT NULL AND completed_at IS NOT NULL)",
    )
    op.drop_column("media_requests", "delivered_at")
    op.drop_column("media_requests", "telegram_chat_id")
