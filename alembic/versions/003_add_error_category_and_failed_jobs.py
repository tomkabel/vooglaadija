"""add error_category to download_jobs and create failed_jobs table

Revision ID: 003
Revises: 002
Create Date: 2026-05-06 17:45:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("error_category", sa.String(length=50), nullable=True),
    )

    op.create_table(
        "failed_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("original_job_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("error_category", sa.String(length=50), nullable=False),
        sa.Column("retry_history", sa.Text(), nullable=True),
        sa.Column("final_error", sa.Text(), nullable=False),
        sa.Column("final_error_category", sa.String(length=50), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries_at_failure", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["original_job_id"], ["download_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_failed_jobs_user_id",
        "failed_jobs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_failed_jobs_error_category",
        "failed_jobs",
        ["error_category"],
        unique=False,
    )
    op.create_index(
        "ix_failed_jobs_expires_at",
        "failed_jobs",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_failed_jobs_expires_at", table_name="failed_jobs")
    op.drop_index("ix_failed_jobs_error_category", table_name="failed_jobs")
    op.drop_index("ix_failed_jobs_user_id", table_name="failed_jobs")
    op.drop_table("failed_jobs")
    op.drop_column("download_jobs", "error_category")
