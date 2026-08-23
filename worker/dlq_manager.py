"""Worker-side dead-letter queue and hard-failure management."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.pubsub_service import get_pubsub_service
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import DLQ_DEPTH, JOBS_COMPLETED, RECOVERIES
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from worker.job_executor import publish_job_status
from worker.retry_scheduler import RetryDecision

logger = get_logger(__name__)


async def update_dlq_depth(db: AsyncSession | None = None) -> None:
    """Update the DLQ depth metric from the failed_jobs table."""
    try:
        from sqlalchemy import func as sa_func

        if db is not None:
            result = await db.execute(sa_func.count(FailedJob.id))
            DLQ_DEPTH.set(result.scalar() or 0)
            return

        session_factory = get_async_session_factory()
        async with session_factory() as session:
            result = await session.execute(sa_func.count(FailedJob.id))
            DLQ_DEPTH.set(result.scalar() or 0)
    except Exception:
        pass


async def move_to_dlq(
    db: AsyncSession,
    job: DownloadJob,
    decision: RetryDecision,
    retry_history: str | None = None,
) -> FailedJob:
    """Write a failed job row with the worker's final-failure metadata."""
    final_error = decision.final_error or job.error or "Unknown worker failure"
    failed = FailedJob(
        id=uuid.uuid4(),
        original_job_id=job.id,
        user_id=job.user_id,
        url=job.url,
        error_category=decision.category.value,
        retry_history=retry_history if retry_history is not None else job.error,
        final_error=final_error,
        final_error_category=decision.category.value,
        retry_count=decision.retry_count,
        max_retries_at_failure=job.max_retries,
        title=job.title,
        failed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(failed)
    await db.flush()
    await update_dlq_depth(db)
    return failed


async def mark_failed_and_move_to_dlq(
    db: AsyncSession,
    job: DownloadJob,
    decision: RetryDecision,
) -> bool:
    """
    Mark a processing job as failed, move it to the dead-letter queue, and publish its final status.

    Parameters:
        job (DownloadJob): The processing job to mark as failed.
        decision (RetryDecision): The failure and retry decision containing error details and category.

    Returns:
        bool: `False` after processing completes.
    """
    active_job_id = job.id
    final_error = decision.final_error or str(job.error or "Unknown worker failure")
    accumulated = decision.accumulated_error or final_error

    failed_result = await db.execute(
        update(DownloadJob)
        .where(
            DownloadJob.id == active_job_id,
            DownloadJob.status == "processing",
        )
        .values(
            status="failed",
            error=accumulated,
            last_error=accumulated,
            error_category=decision.category.value,
            completed_at=datetime.now(UTC),
        ),
    )
    if int(getattr(failed_result, "rowcount", 0) or 0) == 0:
        logger.warning(
            "job_failed_update_skipped_not_processing",
            job_id=str(active_job_id),
        )
        JOBS_COMPLETED.labels(status="failed").inc()
        await db.commit()
        return False

    JOBS_COMPLETED.labels(status="failed").inc()
    await db.commit()

    await move_to_dlq(
        db,
        job,
        decision,
        retry_history=accumulated,
    )
    await db.commit()

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    failed_job = select_result.scalar_one_or_none()
    if failed_job:
        await publish_job_status(failed_job)

    logger.info("job_moved_to_dlq", job_id=str(active_job_id), category=decision.category.value)
    return False


async def reset_stuck_jobs(timeout_minutes: int = 10) -> int:
    """
    Reset jobs that have remained in ``processing`` status beyond the timeout.

    Parameters:
        timeout_minutes (int): Maximum number of minutes a job may remain unchanged
            before it is marked as failed.

    Returns:
        int: Number of jobs reset.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    session_factory = get_async_session_factory()

    async with session_factory() as db:
        result = await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == "processing",
                DownloadJob.updated_at < cutoff,
            )
            .values(
                status="failed",
                error="Job timed out",
                last_error="Job timed out",
                error_category="timeout",
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            .returning(DownloadJob.id, DownloadJob.user_id)
            .execution_options(synchronize_session=False),
        )
        affected = result.fetchall()
        if not affected:
            return 0

        await db.commit()

        pubsub = get_pubsub_service()
        for job_id, user_id in affected:
            try:
                now_iso = datetime.now(UTC).isoformat()
                await pubsub.publish_job_status(
                    user_id,
                    {
                        "id": str(job_id),
                        "status": "failed",
                        "error": "Job timed out",
                        "error_category": "timeout",
                        "updated_at": now_iso,
                    },
                )
            except Exception as e:
                logger.warning(
                    "status_publish_failed",
                    job_id=str(job_id),
                    error=str(e),
                )

        count = len(affected)
        if count > 0:
            RECOVERIES.labels(reason="zombie_sweep_recovery").inc(amount=count)
            logger.warning("reset_stuck_jobs", count=count, timeout_minutes=timeout_minutes)

        return count


async def cleanup_expired_dlq() -> int:
    """Delete expired DLQ entries."""
    session_factory = get_async_session_factory()
    async with session_factory() as db:
        now = datetime.now(UTC)
        result = await db.execute(select(FailedJob).where(FailedJob.expires_at < now))
        expired = result.scalars().all()
        count = len(expired)
        for entry in expired:
            await db.delete(entry)
        await db.commit()
        if count > 0:
            logger.info("dlq_cleanup_completed", purged_entries=count)
        return count
