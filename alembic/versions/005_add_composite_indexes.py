"""add composite indexes

Revision ID: 005
Revises: 004
Create Date: 2026-06-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY ix_download_jobs_user_id_status "
                    "ON download_jobs (user_id, status)"
                )
            )
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY ix_download_jobs_user_id_created_at "
                    "ON download_jobs (user_id, created_at DESC)"
                )
            )
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY ix_download_jobs_status_updated_at "
                    "ON download_jobs (status, updated_at)"
                )
            )
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY ix_failed_jobs_user_id_failed_at "
                    "ON failed_jobs (user_id, failed_at DESC)"
                )
            )
            op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_users_email"))
        return

    op.create_index(
        "ix_download_jobs_user_id_status",
        "download_jobs",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_download_jobs_user_id_created_at",
        "download_jobs",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_download_jobs_status_updated_at",
        "download_jobs",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_failed_jobs_user_id_failed_at",
        "failed_jobs",
        ["user_id", sa.text("failed_at DESC")],
        unique=False,
    )
    op.drop_index("ix_users_email", table_name="users")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_failed_jobs_user_id_failed_at")
            )
            op.execute(
                sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_download_jobs_status_updated_at")
            )
            op.execute(
                sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_download_jobs_user_id_created_at")
            )
            op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_download_jobs_user_id_status"))
            op.execute(sa.text("CREATE INDEX CONCURRENTLY ix_users_email ON users (email)"))
        return

    op.drop_index("ix_failed_jobs_user_id_failed_at", table_name="failed_jobs")
    op.drop_index("ix_download_jobs_status_updated_at", table_name="download_jobs")
    op.drop_index("ix_download_jobs_user_id_created_at", table_name="download_jobs")
    op.drop_index("ix_download_jobs_user_id_status", table_name="download_jobs")
    op.create_index("ix_users_email", "users", ["email"], unique=False)
