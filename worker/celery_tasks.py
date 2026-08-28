"""Celery tasks for Vooglaadija download processing.

Replaces the hand-rolled BRPOP worker with durable, retryable Celery tasks.
Each task maps to the equivalent function in the legacy worker:

  process_next_job()  → process_download()
  retry scheduling    → retry_download()
  DLQ handling       → handle_failed_job()
  cleanup_expired    → cleanup_expired_jobs()
  zombie sweep       → requeue_stuck_jobs()
"""

import asyncio
import time as _time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import select, update

from app.services.error_classifier import classify_error
from core.config import settings
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import (
    DLQ_DEPTH,
    JOB_DURATION_SECONDS,
    JOBS_COMPLETED,
    QUEUE_DEPTH,
)
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from worker.browser_executor import (
    extract_media as extract_media_browser,
    select_executor,
)
from worker.job_executor import publish_job_status

logger = get_task_logger(__name__)
_sync_logger = get_logger(__name__)


def _run_async(coro):
    """Run an async coroutine synchronously within a Celery task."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task(
    bind=True,
    name="worker.celery_tasks.process_download",
    queue="downloads",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=540,
    time_limit=600,
)
def process_download(self, job_id: str) -> dict:
    """Process a single download job.

    Equivalent to worker.processor.process_next_job() in the legacy BRPOP worker.
    """
    start_time = _time.time()
    session_factory = get_async_session_factory()

    async def _process():
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id, DownloadJob.status == "pending")
                .values(status="processing", updated_at=datetime.now(UTC))
                .returning(DownloadJob)
                .execution_options(synchronize_session=False),
            )
            job = result.scalar_one_or_none()
            if job is None:
                return {"status": "skipped", "reason": "not_found_or_not_pending"}
            await db.commit()

            try:
                await publish_job_status(job)
                execution_result = await _execute_job(db, job, start_time)
                await _handle_result(db, job, execution_result)
                return {"status": execution_result["status"], "job_id": job_id}

            except asyncio.CancelledError:
                await _requeue_job(job.id, db)
                raise

    return _run_async(_process())


async def _execute_job(db, job: DownloadJob, start_time: float) -> dict:
    """Execute the download for a job using the appropriate executor."""
    from app.services.circuit_breaker import extract_media_with_circuit_breaker

    try:
        executor_kind = select_executor(job.url)

        if executor_kind == "browser":
            file_path, file_name, title = await extract_media_browser(
                job.url,
                settings.storage_path,
                progress_callback=lambda data: _publish_progress(job.user_id, job.id, data),
            )
        else:
            file_path, file_name, title = await extract_media_with_circuit_breaker(
                job.url,
                settings.storage_path,
                progress_callback=lambda data: _publish_progress(job.user_id, job.id, data),
            )

        await db.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job.id, DownloadJob.status == "processing")
            .values(
                status="completed",
                file_path=file_path,
                file_name=file_name,
                title=title,
                completed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=settings.file_expire_hours),
            )
        )
        await db.commit()

        refreshed = await db.execute(select(DownloadJob).where(DownloadJob.id == job.id))
        completed_job = refreshed.scalar_one_or_none()
        if completed_job:
            await publish_job_status(completed_job)

        JOBS_COMPLETED.labels(status="success").inc()
        return {"status": "completed", "job_id": str(job.id)}

    except Exception as e:
        return {"status": "error", "job_id": str(job.id), "error": e}


async def _handle_result(db, job: DownloadJob, result: dict) -> None:
    """Handle the execution result — retry or move to DLQ."""
    if result["status"] == "completed":
        return

    error = result["error"]
    classification = classify_error(str(error))

    if classification.retryable and job.retry_count < job.max_retries:
        retry_count = job.retry_count + 1
        delay = _calculate_backoff(retry_count, classification.base_delay)

        await db.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job.id, DownloadJob.status == "processing")
            .values(
                status="pending",
                retry_count=retry_count,
                next_retry_at=datetime.now(UTC) + timedelta(seconds=delay),
                error=str(error),
                last_error=str(error),
                error_category=classification.category.value,
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

        process_download.apply_async(
            args=[str(job.id)],
            countdown=delay,
            queue="retries",
        )
    else:
        await _move_to_dlq(db, job, error, classification)


async def _move_to_dlq(db, job: DownloadJob, error: Exception, classification) -> None:
    """Move a permanently failed job to the dead-letter queue.

    Both the DownloadJob status update and FailedJob insertion happen in a
    single transaction so either both records persist or neither does.
    """
    failed = FailedJob(
        id=__import__("uuid").uuid4(),
        original_job_id=job.id,
        user_id=job.user_id,
        url=job.url,
        error_category=classification.category.value,
        final_error=str(error),
        final_error_category=classification.category.value,
        retry_count=job.retry_count,
        max_retries_at_failure=job.max_retries,
        title=job.title,
        failed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    await db.execute(
        update(DownloadJob)
        .where(DownloadJob.id == job.id, DownloadJob.status == "processing")
        .values(
            status="failed",
            error=str(error),
            last_error=str(error),
            error_category=classification.category.value,
            completed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    db.add(failed)
    await db.commit()

    refreshed = await db.execute(select(DownloadJob).where(DownloadJob.id == job.id))
    failed_job = refreshed.scalar_one_or_none()
    if failed_job:
        await publish_job_status(failed_job)

    JOBS_COMPLETED.labels(status="failed").inc()
    DLQ_DEPTH.inc()


async def _requeue_job(job_id: UUID, db) -> None:
    """Requeue a job during shutdown or cancellation."""
    await db.execute(
        update(DownloadJob)
        .where(DownloadJob.id == job_id, DownloadJob.status == "processing")
        .values(status="pending", updated_at=datetime.now(UTC))
    )
    await db.commit()


def _publish_progress(user_id: UUID, job_id: UUID, data: dict) -> None:
    """Publish progress update via pub/sub."""
    try:
        from app.services.pubsub_service import get_pubsub_service

        pubsub = get_pubsub_service()

        async def _publish():
            await pubsub.publish_job_progress(
                user_id,
                {
                    "id": str(job_id),
                    "progress": {
                        "percent": data.get("percent"),
                        "speed": data.get("speed"),
                        "eta": data.get("eta"),
                        "downloaded_bytes": data.get("downloaded_bytes"),
                        "total_bytes": data.get("total_bytes"),
                    },
                },
            )

        _run_async(_publish())
    except Exception:
        pass


def _calculate_backoff(retry_count: int, base_delay: float) -> float:
    """Calculate exponential backoff with jitter."""
    import random

    exp_delay = base_delay * (2 ** (retry_count - 1))
    jitter = random.uniform(0, exp_delay * 0.5)
    return min(exp_delay + jitter, 300)


@shared_task(
    name="worker.celery_tasks.retry_download",
    queue="retries",
    acks_late=True,
)
def retry_download(job_id: str) -> dict:
    """Retry a previously failed download job.

    Scheduled by process_download when a retryable error occurs.
    """
    return process_download(job_id)


@shared_task(
    name="worker.celery_tasks.handle_failed_job",
    queue="dlq",
)
def handle_failed_job(job_id: str, error_info: str) -> dict:
    """Handle a permanently failed job (DLQ processing).

    Can be extended for alerting, user notification, etc.
    """
    _sync_logger.info("job_moved_to_dlq", job_id=job_id, error=error_info)
    return {"status": "dlq_processed", "job_id": job_id}


@shared_task(
    name="worker.celery_tasks.cleanup_expired_jobs",
)
def cleanup_expired_jobs() -> dict:
    """Remove expired completed jobs and their files.

    Replaces the legacy cleanup_expired_jobs() from worker/main.py.
    Scheduled via Celery Beat every 5 minutes.
    """
    import os

    from core.utils.security import validate_path

    session_factory = get_async_session_factory()
    downloads_dir = os.path.join(settings.storage_path, "downloads")

    async def _cleanup():
        async with session_factory() as db:
            now = datetime.now(UTC)
            result = await db.execute(
                select(DownloadJob).where(
                    DownloadJob.expires_at < now,
                    DownloadJob.status == "completed",
                )
            )
            expired_jobs = result.scalars().all()
            cleanup_count = 0

            for job in expired_jobs:
                if job.file_path:
                    try:
                        safe_path = validate_path(downloads_dir, job.file_path)
                    except (ValueError, PermissionError):
                        continue
                    if os.path.exists(safe_path):
                        try:
                            os.remove(safe_path)
                        except OSError:
                            continue
                    await db.delete(job)
                    cleanup_count += 1
                else:
                    await db.delete(job)
                    cleanup_count += 1

            await db.commit()
            return {"cleaned": cleanup_count}

    return _run_async(_cleanup())


@shared_task(
    name="worker.celery_tasks.cleanup_dlq",
)
def cleanup_dlq() -> dict:
    """Remove expired DLQ entries older than 7 days."""
    session_factory = get_async_session_factory()

    async def _cleanup():
        async with session_factory() as db:
            now = datetime.now(UTC)
            result = await db.execute(
                select(FailedJob).where(FailedJob.expires_at < now)
            )
            expired = result.scalars().all()
            count = len(expired)
            for entry in expired:
                await db.delete(entry)
            await db.commit()
            DLQ_DEPTH.set(0)
            return {"dlq_cleaned": count}

    return _run_async(_cleanup())


@shared_task(
    name="worker.celery_tasks.requeue_stuck_jobs",
)
def requeue_stuck_jobs(timeout_minutes: int = 15) -> dict:
    """Requeue jobs stuck in processing state (zombie sweep).

    Replaces the legacy requeue_stuck_jobs() from worker/zombie_sweeper.py.
    Scheduled via Celery Beat every 15 minutes.
    """
    session_factory = get_async_session_factory()

    async def _sweep():
        cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
        async with session_factory() as db:
            result = await db.execute(
                update(DownloadJob)
                .where(
                    DownloadJob.status == "processing",
                    DownloadJob.updated_at < cutoff,
                )
                .values(
                    status="pending",
                    updated_at=datetime.now(UTC),
                )
                .returning(DownloadJob.id)
            )
            requeued_ids = result.scalars().all()
            await db.commit()
            count = len(requeued_ids)
            if count > 0:
                for job_id in requeued_ids:
                    process_download.apply_async(args=[str(job_id)], queue="downloads")
            return {"requeued": count}

    return _run_async(_sweep())


@shared_task(
    name="worker.celery_tasks.enqueue_pending",
)
def enqueue_pending() -> dict:
    """Enqueue all pending jobs in the database that are not already queued.

    Filters out retry-scheduled jobs (next_retry_at in the future) to avoid
    duplicate broker messages. Idempotent — process_download's atomic UPDATE
    WHERE status='pending' guard prevents double-processing.
    """
    session_factory = get_async_session_factory()

    async def _enqueue():
        async with session_factory() as db:
            now = datetime.now(UTC)
            result = await db.execute(
                select(DownloadJob.id).where(
                    DownloadJob.status == "pending",
                    (DownloadJob.next_retry_at.is_(None)) | (DownloadJob.next_retry_at <= now),
                )
            )
            pending_ids = result.scalars().all()
            count = 0
            for job_id in pending_ids:
                process_download.apply_async(args=[str(job_id)], queue="downloads")
                count += 1
            return {"enqueued": count}

    return _run_async(_enqueue())
