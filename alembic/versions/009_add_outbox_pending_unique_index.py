"""add uq_outbox_pending_job_id partial unique index

Revision ID: 009
Revises: 008
Create Date: 2026-08-11

Adds the partial unique index ``uq_outbox_pending_job_id`` that guarantees at
most one *pending* outbox row per ``job_id``. The ``Outbox`` model already
declares this index, but no migration shipped it, so deployed databases
(PostgreSQL and SQLite) never enforced the idempotency constraint the
application relies on. This migration brings them in line with the model.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_outbox_pending_job_id"
_PENDING_WHERE = "status = 'pending'"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Earlier migrations could leave multiple pending rows for one job_id.
    # The partial unique index below would fail to create on such a database,
    # so deterministically reconcile: keep the earliest row per job_id and drop
    # the rest. The surviving pending row is still delivered by the relay.
    # PostgreSQL has no min(uuid) aggregate, so dedupe with a window function
    # (works on both PostgreSQL and SQLite) instead of MIN(id).
    op.execute(
        sa.text(
            "DELETE FROM outbox WHERE status = 'pending' AND id IN ("
            "SELECT id FROM ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY job_id ORDER BY created_at, id) AS rn "
            "FROM outbox WHERE status = 'pending'"
            ") ranked WHERE rn > 1)"
        )
    )

    if dialect == "postgresql":
        op.create_index(
            _INDEX_NAME,
            "outbox",
            ["job_id"],
            unique=True,
            postgresql_where=sa.text(_PENDING_WHERE),
        )
    elif dialect == "sqlite":
        # SQLite partial indexes are only expressible via raw DDL.
        op.execute(
            sa.text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
                f"ON outbox (job_id) WHERE {_PENDING_WHERE}"
            )
        )
    else:
        op.create_index(
            _INDEX_NAME,
            "outbox",
            ["job_id"],
            unique=True,
            sqlite_where=sa.text(_PENDING_WHERE),
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.drop_index(
            _INDEX_NAME,
            table_name="outbox",
            postgresql_where=sa.text(_PENDING_WHERE),
        )
    else:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
