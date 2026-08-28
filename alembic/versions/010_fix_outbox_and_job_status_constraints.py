"""fix outbox and download_jobs status check constraints

Revision ID: 010
Revises: 009
Create Date: 2026-08-24

Migration 007 shipped two CHECK constraints whose allowed values did not match
the values the application actually writes:

- ``chk_outbox_status`` allowed only ``('pending', 'published')``, but the
  outbox relay transitions rows to ``processed``/``failed``. Every relay cycle
  therefore raised ``CheckViolationError`` (thousands of log lines) because the
  ``UPDATE ... SET status='processed'`` was rejected and the row was never
  closed, so it was re-selected on the next poll.

- ``chk_download_jobs_status`` omitted ``'cancelled'``, yet the download
  deletion route permits deleting (and therefore marking) jobs as ``cancelled``.
  This would surface as a ``CheckViolationError`` the moment the first job is
  cancelled.

This migration rebuilds both constraints to match the real lifecycle values.
It is idempotent: the down-revision check and the ``IF EXISTS`` guards mean it
can be applied safely whether or not the old constraint shape is present.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNLOAD_JOB_STATUSES = (
    "pending",
    "processing",
    "completed",
    "failed",
    "deferred",
    "cancelled",
)
_OUTBOX_STATUSES = ("pending", "processed", "failed")


def upgrade() -> None:
    bind = op.get_bind()

    # --- download_jobs.status: add 'cancelled' ------------------------------
    jobs_in = ", ".join(f"'{s}'" for s in _DOWNLOAD_JOB_STATUSES)
    # SQLite does not support ALTER TABLE ADD/DROP CHECK or ADD CONSTRAINT, so
    # the corrected constraint can only be (re)built on PostgreSQL. On SQLite
    # the schema is created by create_all() and the model/enum already enforce
    # the allowed values, so skipping here is safe.
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text("ALTER TABLE download_jobs DROP CONSTRAINT IF EXISTS chk_download_jobs_status")
        )
        op.execute(
            sa.text(
                f"ALTER TABLE download_jobs "
                f"ADD CONSTRAINT chk_download_jobs_status "
                f"CHECK (status IN ({jobs_in}))"
            )
        )

    # --- outbox.status: allow the real lifecycle ---------------------------
    outbox_in = ", ".join(f"'{s}'" for s in _OUTBOX_STATUSES)
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE outbox DROP CONSTRAINT IF EXISTS chk_outbox_status"))
        op.execute(
            sa.text(
                f"ALTER TABLE outbox "
                f"ADD CONSTRAINT chk_outbox_status "
                f"CHECK (status IN ({outbox_in}))"
            )
        )

        # Any rows already carrying a now-forbidden value are reconciled so the
        # rebuilt constraint validates immediately.
        op.execute(
            sa.text(f"UPDATE outbox SET status = 'pending' WHERE status NOT IN ({outbox_in})")  # noqa: S608
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text("ALTER TABLE outbox DROP CONSTRAINT IF EXISTS chk_outbox_status"))
    op.execute(
        sa.text("ALTER TABLE download_jobs DROP CONSTRAINT IF EXISTS chk_download_jobs_status")
    )
