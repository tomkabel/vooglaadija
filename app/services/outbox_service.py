import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.outbox import Outbox

_PENDING_STATUS = "pending"


async def write_job_to_outbox(
    db: AsyncSession,
    job_id: UUID,
    event_type: str = "enqueue_download",
    payload: str | None = None,
) -> Outbox | None:
    """
    Add a pending outbox entry for a job within the current transaction.

    Duplicate pending entries are ignored, including concurrent duplicates.

    Returns:
        Outbox | None: The newly created outbox entry, or `None` when a pending
        entry already exists.
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
    try:
        # Run the insert inside a savepoint so a concurrent writer's
        # IntegrityError (on the partial unique index) only rolls back this
        # attempt and leaves the enclosing transaction committable. Plain
        # `db.flush()` would poison the outer transaction and make the caller's
        # `commit()` fail instead of handling the duplicate idempotently.
        async with db.begin_nested():
            db.add(outbox_entry)
            await db.flush()
    except IntegrityError:
        # A concurrent writer inserted the pending row first. Rolling the
        # savepoint back already detaches our pending object from the session,
        # so guard the expunge: a second call would raise InvalidRequestError
        # and poison the enclosing transaction we are trying to keep alive.
        try:
            db.expunge(outbox_entry)
        except InvalidRequestError:
            pass
        confirm = await db.execute(
            select(Outbox.id).where(
                Outbox.job_id == job_id,
                Outbox.status == _PENDING_STATUS,
            )
        )
        if confirm.scalars().one_or_none() is None:
            # No pending row and no insert succeeded: surface the real failure.
            raise
        return None
    return outbox_entry
