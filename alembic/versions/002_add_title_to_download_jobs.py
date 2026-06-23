"""add title column to download_jobs

Revision ID: 002
Revises: 001
Create Date: 2026-05-06 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("title", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("download_jobs", "title")
