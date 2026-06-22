"""add failed job original job id index

Revision ID: 006
Revises: 005
Create Date: 2026-06-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    "ix_failed_jobs_original_job_id ON failed_jobs (original_job_id)"
                )
            )
        return

    op.create_index(
        "ix_failed_jobs_original_job_id",
        "failed_jobs",
        ["original_job_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_failed_jobs_original_job_id"))
        return

    op.drop_index("ix_failed_jobs_original_job_id", table_name="failed_jobs")
