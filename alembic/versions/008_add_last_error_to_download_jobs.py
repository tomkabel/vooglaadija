"""add last_error column to download_jobs

Revision ID: 008
Revises: 007
Create Date: 2026-06-22

Adds a last_error column to store only the most recent error message,
replacing the concatenated error pattern in the existing error column.
Migrates existing concatenated data to keep only the last error segment.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE download_jobs "
                "SET last_error = CASE "
                "  WHEN error IS NULL THEN NULL "
                "  WHEN error LIKE '% → %' "
                "    THEN reverse(split_part(reverse(error), ' → ', 1)) "
                "  ELSE error "
                "END"
            )
        )


def downgrade() -> None:
    op.drop_column("download_jobs", "last_error")
