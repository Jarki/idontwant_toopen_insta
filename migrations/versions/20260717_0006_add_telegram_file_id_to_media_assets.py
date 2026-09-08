"""add Telegram file IDs to media assets

Revision ID: 20260717_0006
Revises: 20260716_0005
Create Date: 2026-07-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0006"
down_revision: str | None = "20260716_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("telegram_file_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_assets", "telegram_file_id")
