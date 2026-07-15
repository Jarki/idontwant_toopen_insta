"""create reels table

Revision ID: 20260707_0001
Revises:
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260707_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIRED_REELS_COLUMNS = {
    "id",
    "title",
    "description",
    "filepath",
    "url",
    "like_count",
    "created_at",
    "comments",
}


def _validate_existing_reels_table(inspector: sa.Inspector) -> None:
    columns = inspector.get_columns("reels")
    existing_columns = {column["name"] for column in columns}
    missing_columns = REQUIRED_REELS_COLUMNS - existing_columns
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        msg = f"Existing reels table is missing required columns: {missing}"
        raise RuntimeError(msg)

    primary_key_columns = set(
        inspector.get_pk_constraint("reels").get("constrained_columns") or []
    )
    if "id" not in primary_key_columns:
        unique_column_sets = [
            set(constraint["column_names"] or [])
            for constraint in inspector.get_unique_constraints("reels")
        ]
        if {"id"} not in unique_column_sets:
            msg = (
                "Existing reels table must define id as a primary key or unique column"
            )
            raise RuntimeError(msg)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("reels"):
        _validate_existing_reels_table(inspector)
        return

    op.create_table(
        "reels",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("filepath", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("comments", sa.String(), nullable=False),
    )


def downgrade() -> None:
    # The initial downgrade is intentionally data-preserving. Removing this
    # revision from alembic_version must not drop cached reel metadata.
    pass
