import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.outbox import Outbox

_PENDING_STATUS = "pending"


async def write_job_to_outbox(
    db: AsyncSession,
    job_id: UUID,
    event_type: str = "enqueue_download",
    payload: str | None = None,
) -> Outbox | None:
    """Write a job to the outbox in the same transaction as the main entity.

    This ensures atomicity - if the transaction commits, the outbox entry exists.
    The worker will process this entry and mark it as processed.

    Idempotent under concurrency:

    1. Application-layer pre-check: SELECT for existing pending entry.
    2. Database-layer guarantee: partial unique index
       ``uq_outbox_pending_job_id`` (job_id) WHERE status='pending' rejects
       duplicate inserts at commit time. If the SELECT misses a race, the
       IntegrityError on flush is treated as "already pending" and returns
       ``None`` without re-raising, so the outer transaction can commit.
    """
    result = await db.execute(
        select(Outbox).where(
            Outbox.job_id == job_id,
            Outbox.status == _PENDING_STATUS,
        ),
    )
    existing = result.scalars().one_or_none()
    if existing is not None:
        return None

    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type=event_type,
        payload=payload,
        status=_PENDING_STATUS,
    )
    db.add(outbox_entry)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent writer beat us to the insert via the partial unique
        # index. Roll back the just-flushed attempt only; the outer
        # transaction (which may include a DownloadJob insert) must still
        # commit. Detach our object so subsequent add/commit is clean.
        db.expunge(outbox_entry)
        return None
    return outbox_entry
