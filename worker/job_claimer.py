"""Job queue pop, atomic claim, and heartbeat helpers for the worker."""

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from core.models.download_job import DownloadJob
from core.queue import redis_client

logger = get_logger(__name__)


def normalize_job_id(job_id: UUID | str | bytes | None) -> UUID | None:
    """Normalize worker job identifiers to UUID values."""
    if job_id is None:
        return None
    if isinstance(job_id, UUID):
        return job_id
    if isinstance(job_id, bytes):
        job_id = job_id.decode()
    try:
        return UUID(str(job_id))
    except (TypeError, ValueError):
        logger.warning("invalid_job_id", job_id=str(job_id))
        return None


async def next_job_id(job_id: UUID | str | bytes | None = None) -> UUID | None:
    """Return an explicit job id or pop the next id from the download queue."""
    if job_id is None:
        try:
            job_id = await redis_client.rpop("download_queue")
        except Exception as e:
            logger.warning("redis_rpop_failed", error=str(e))
            return None
        if not job_id:
            return None
    return normalize_job_id(job_id)


async def claim_next(db: AsyncSession, job_id: UUID | str | bytes) -> DownloadJob | None:
    """Atomically claim a pending job and return the claimed ORM object."""
    normalized_job_id = normalize_job_id(job_id)
    if normalized_job_id is None:
        return None

    result = await db.execute(
        update(DownloadJob)
        .where(DownloadJob.id == normalized_job_id, DownloadJob.status == "pending")
        .values(status="processing", updated_at=datetime.now(UTC))
        .returning(DownloadJob)
        .execution_options(synchronize_session=False),
    )
    job = result.scalar_one_or_none()
    await db.commit()
    return job


async def heartbeat(db: AsyncSession, job_id: UUID) -> None:
    """Update a processing job heartbeat timestamp."""
    await db.execute(
        update(DownloadJob).where(DownloadJob.id == job_id).values(updated_at=datetime.now(UTC)),
    )
    await db.commit()


async def periodic_heartbeat(
    db_factory,
    job_id: UUID,
    stop_event: asyncio.Event,
) -> None:
    """Send heartbeats every 30 seconds until the stop event is set."""
    try:
        async with db_factory() as hb_db:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=30.0)
                    break
                except TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                with contextlib.suppress(Exception):
                    await hb_db.execute(
                        update(DownloadJob)
                        .where(DownloadJob.id == job_id)
                        .values(updated_at=datetime.now(UTC)),
                    )
                    await hb_db.commit()
    except asyncio.CancelledError:
        pass
