"""Job factory for creating download jobs with transactional outbox.

Shared utility used by demo_login auto-submit and chaos-lab bulk submit
to create jobs atomically with proper outbox entries.
"""

import uuid
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.outbox_service import write_job_to_outbox
from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.queue import enqueue_job

logger = get_logger(__name__)


async def create_demo_job(
    db: AsyncSession,
    user_id: UUID,
    url: str,
    *,
    enqueue: bool = True,
    stagger_delay: float = 0.0,
) -> DownloadJob | None:
    """
    Create a pending download job and optionally enqueue it for processing.
    
    Parameters:
        db (AsyncSession): Database session used to persist the job.
        user_id (UUID): Identifier of the user associated with the job.
        url (str): Video URL to download.
        enqueue (bool): Whether to enqueue the job after it is created.
        stagger_delay (float): Number of seconds to wait before enqueueing.
    
    Returns:
        DownloadJob | None: The created job, or None if creation fails.
    """
    import asyncio

    try:
        job_id = uuid.uuid4()
        job = DownloadJob(
            id=job_id,
            user_id=user_id,
            url=url,
            status="pending",
        )
        db.add(job)

        await write_job_to_outbox(db, job_id)
        await db.commit()
        await db.refresh(job)

        if enqueue:
            if stagger_delay > 0:
                await asyncio.sleep(stagger_delay)
            await enqueue_job(job_id)

        logger.info("demo_job_created", job_id=str(job_id), url=url[:60])
        return job

    except Exception as e:
        logger.error("demo_job_creation_failed", url=url[:60], error=str(e))
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def create_demo_jobs_bulk(
    db: AsyncSession,
    user_id: UUID,
    urls: list[str],
    *,
    enqueue: bool = True,
    stagger_delay: float = 0.2,
) -> list[DownloadJob]:
    """Create multiple demo download jobs with staggered enqueue.

    Args:
        db: Database session
        user_id: UUID of the user
        urls: List of video URLs to submit
        enqueue: If True, enqueue with stagger
        stagger_delay: Seconds between each enqueue

    Returns:
        List of successfully created DownloadJob objects

    """
    created: list[DownloadJob] = []

    for i, url in enumerate(urls):
        delay = stagger_delay * i if enqueue else 0.0
        job = await create_demo_job(
            db,
            user_id,
            url,
            enqueue=enqueue,
            stagger_delay=delay,
        )
        if job:
            created.append(job)

    return created
