"""widen media_assets.file_size_bytes from Integer to BigInteger

Revision ID: 20260715_0004
Revises: 20260710_0003
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0004"
down_revision: str | None = "20260710_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "media_assets",
        "file_size_bytes",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "media_assets",
        "file_size_bytes",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        nullable=True,
        postgresql_using="file_size_bytes::integer",
    )
