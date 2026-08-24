"""add personal_access_tokens

Revision ID: 011
Revises: 010
Create Date: 2026-08-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("hashed_token", sa.String(255), nullable=False),
        sa.Column("scopes", sa.Text(), server_default=sa.text("'read:downloads'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_personal_access_tokens_user_id", "personal_access_tokens", ["user_id"])
    op.create_index("ix_personal_access_tokens_hashed_token", "personal_access_tokens", ["hashed_token"])
    op.create_unique_constraint(
        "uq_personal_access_tokens_hashed_token", "personal_access_tokens", ["hashed_token"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_personal_access_tokens_hashed_token", "personal_access_tokens", type_="unique")
    op.drop_index("ix_personal_access_tokens_hashed_token", table_name="personal_access_tokens")
    op.drop_index("ix_personal_access_tokens_user_id", table_name="personal_access_tokens")
    op.drop_table("personal_access_tokens")
