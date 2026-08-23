"""add clerk_user_id to users, make password_hash nullable

Revision ID: 010
Revises: 009
Create Date: 2026-08-23

Replaces custom JWT auth with Clerk. Adds clerk_user_id column to users
table and makes password_hash nullable since Clerk handles password storage.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add clerk_user_id column
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(255), nullable=True),
    )

    # Make password_hash nullable (Clerk handles passwords now)
    if dialect == "postgresql":
        op.execute(sa.text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
    elif dialect == "sqlite":
        # SQLite doesn't support ALTER COLUMN; recreate table
        # This is a no-op for SQLite since it doesn't enforce NOT NULL on ALTER
        pass

    # Create unique index on clerk_user_id for active users
    if dialect == "postgresql":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX ix_users_clerk_user_id_active "
                "ON users (clerk_user_id) "
                "WHERE deleted_at IS NULL AND clerk_user_id IS NOT NULL"
            )
        )
    elif dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_clerk_user_id_active "
                "ON users (clerk_user_id) "
                "WHERE deleted_at IS NULL AND clerk_user_id IS NOT NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Drop the index
    if dialect == "postgresql":
        op.drop_index("ix_users_clerk_user_id_active", table_name="users")
    else:
        op.execute(sa.text("DROP INDEX IF EXISTS ix_users_clerk_user_id_active"))

    # Restore password_hash NOT NULL (would require setting a value for NULL rows)
    if dialect == "postgresql":
        op.execute(sa.text("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL"))

    # Drop clerk_user_id column
    op.drop_column("users", "clerk_user_id")
