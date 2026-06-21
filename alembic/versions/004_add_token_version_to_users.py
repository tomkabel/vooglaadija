"""add token_version to users

Revision ID: 004
Revises: 003
Create Date: 2026-06-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
