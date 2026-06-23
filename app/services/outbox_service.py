import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.outbox import Outbox


async def write_job_to_outbox(
    db: AsyncSession,
    job_id: UUID,
    event_type: str = "enqueue_download",
    payload: str | None = None,
) -> Outbox | None:
    """Write a job to the outbox in the same transaction as the main entity.

    This ensures atomicity - if the transaction commits, the outbox entry exists.
    The worker will process this entry and mark it as processed.

    Idempotent: checks for existing pending outbox record for job_id before inserting.
    """
    # Check for existing pending outbox entry for this job to avoid duplicates
    result = await db.execute(
        select(Outbox).where(
            Outbox.job_id == job_id,
            Outbox.status == "pending",
        )
    )
    existing = result.scalars().one_or_none()
    if existing is not None:
        # Already has a pending outbox entry for this job, skip duplicate
        return None

    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=job_id,
        event_type=event_type,
        payload=payload,
        status="pending",
    )
    db.add(outbox_entry)
    return outbox_entry
