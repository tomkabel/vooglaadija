"""add check constraints and foreign key

Revision ID: 007
Revises: 006
Create Date: 2026-06-22

Adds CHECK constraints on download_jobs.status and outbox.status,
and a FOREIGN KEY on outbox.job_id referencing download_jobs.id.

Pre-checks and cleans any existing invalid data before adding constraints.
PostgreSQL uses NOT VALID + VALIDATE for production safety.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pre_check_data(bind: sa.engine.Connection) -> None:
    """Clean invalid data before adding constraints."""
    # Clean invalid download_jobs statuses
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM download_jobs "
            "WHERE status NOT IN "
            "('pending', 'processing', 'completed', 'failed', 'deferred')"
        )
    ).scalar()
    if result and result > 0:
        bind.execute(
            sa.text(
                "UPDATE download_jobs SET "
                "status = 'failed', "
                "error = CONCAT(COALESCE(error, ''), "
                "' | Remediated by migration 006: invalid status value') "
                "WHERE status NOT IN "
                "('pending', 'processing', 'completed', 'failed', 'deferred')"
            )
        )

    # Clean invalid outbox statuses
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM outbox "
            "WHERE status NOT IN ('pending', 'published')"
        )
    ).scalar()
    if result and result > 0:
        bind.execute(
            sa.text(
                "UPDATE outbox SET status = 'pending' "
                "WHERE status NOT IN ('pending', 'published')"
            )
        )

    # Clean orphaned outbox entries (job_id points to non-existent download_job)
    result = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM outbox o "
            "LEFT JOIN download_jobs d ON o.job_id = d.id "
            "WHERE d.id IS NULL"
        )
    ).scalar()
    if result and result > 0:
        bind.execute(
            sa.text(
                "DELETE FROM outbox o "
                "WHERE o.id IN ("
                "SELECT o2.id FROM outbox o2 "
                "LEFT JOIN download_jobs d ON o2.job_id = d.id "
                "WHERE d.id IS NULL"
                ")"
            )
        )


def _add_constraints_postgres(bind: sa.engine.Connection) -> None:
    """Add constraints with NOT VALID + VALIDATE for PostgreSQL production safety."""
    # CHECK on download_jobs.status
    op.execute(
        sa.text(
            "ALTER TABLE download_jobs "
            "ADD CONSTRAINT chk_download_jobs_status "
            "CHECK (status IN "
            "('pending', 'processing', 'completed', 'failed', 'deferred')) "
            "NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE download_jobs "
            "VALIDATE CONSTRAINT chk_download_jobs_status"
        )
    )

    # CHECK on outbox.status
    op.execute(
        sa.text(
            "ALTER TABLE outbox "
            "ADD CONSTRAINT chk_outbox_status "
            "CHECK (status IN ('pending', 'published')) "
            "NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE outbox "
            "VALIDATE CONSTRAINT chk_outbox_status"
        )
    )

    # FK on outbox.job_id
    op.execute(
        sa.text(
            "ALTER TABLE outbox "
            "ADD CONSTRAINT fk_outbox_job_id "
            "FOREIGN KEY (job_id) REFERENCES download_jobs(id) "
            "ON DELETE CASCADE "
            "NOT VALID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE outbox "
            "VALIDATE CONSTRAINT fk_outbox_job_id"
        )
    )


def _drop_constraints_postgres() -> None:
    """Drop constraints with PostgreSQL-specific syntax."""
    op.execute(sa.text("ALTER TABLE outbox DROP CONSTRAINT IF EXISTS fk_outbox_job_id"))
    op.execute(sa.text("ALTER TABLE outbox DROP CONSTRAINT IF EXISTS chk_outbox_status"))
    op.execute(
        sa.text(
            "ALTER TABLE download_jobs "
            "DROP CONSTRAINT IF EXISTS chk_download_jobs_status"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    # Step 1: Pre-check and clean invalid data (regular DML, no autocommit needed)
    _pre_check_data(bind)

    # Step 2: Add constraints
    if bind.dialect.name == "postgresql":
        _add_constraints_postgres(bind)
    else:
        # SQLite does not support ALTER TABLE ADD CHECK or ADD FOREIGN KEY.
        # Constraints are PostgreSQL-only. Data pre-checks still run above.
        pass


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        _drop_constraints_postgres()
    else:
        pass
