"""create judgmental animations table

Revision ID: 20260710_0003
Revises: 20260707_0002
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0003"
down_revision: str | None = "20260707_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "judgmental_animations" in inspector.get_table_names():
        msg = "Unexpected pre-existing judgmental_animations table"
        raise RuntimeError(msg)

    op.create_table(
        "judgmental_animations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("file_unique_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("file_id", name="uq_judgmental_animations_file_id"),
        sa.UniqueConstraint(
            "file_unique_id",
            name="uq_judgmental_animations_file_unique_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("judgmental_animations")
