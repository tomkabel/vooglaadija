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
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import (
    DLQ_DEPTH,
    JOBS_COMPLETED,
)
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from worker.browser_executor import (
    extract_media as extract_media_browser,
)
from worker.browser_executor import (
    select_executor,
)
from worker.job_executor import publish_job_status
from worker.retry_scheduler import RetryDecision
from worker.retry_scheduler import evaluate as evaluate_retry

logger = get_task_logger(__name__)
_sync_logger = get_logger(__name__)

_worker_loop: asyncio.AbstractEventLoop | None = None


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine synchronously within a Celery task.

    Uses one persistent event loop per worker process so shared async
    resources (Redis, DB) are never bound to a fresh, per-task loop —
    creating a loop per task would make cross-task Redis/DB reuse fail with
    "Event loop is closed" / "attached to a different loop" errors.
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


@shared_task(
    bind=True,
    name="worker.celery_tasks.process_download",
    queue="downloads",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=540,
    time_limit=600,
)
def process_download(self: Any, job_id: str) -> dict:
    """Process a single download job.

    Equivalent to worker.processor.process_next_job() in the legacy BRPOP worker.
    """
    start_time = _time.time()
    session_factory = get_async_session_factory()

    async def _process() -> dict:
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


async def _execute_job(db: AsyncSession, job: DownloadJob, start_time: float) -> dict:
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


async def _handle_result(db: AsyncSession, job: DownloadJob, result: dict) -> None:
    """Handle the execution result — retry or move to DLQ.

    Retry decisions come from ``worker.retry_scheduler.evaluate`` (the same
    single mechanism the legacy worker used), so the retry budget
    (``retry_count`` vs ``max_retries``), backoff policy and error metadata
    stay consistent with the rest of the system.
    """
    if result["status"] == "completed":
        return

    error = result["error"]
    decision = evaluate_retry(job, error)

    if not decision.is_final:
        retry_count = job.retry_count + 1
        delay = decision.delay_seconds if decision.delay_seconds is not None else 10.0
        next_retry_at = decision.next_retry_at or (datetime.now(UTC) + timedelta(seconds=delay))
        error_text = decision.accumulated_error or str(error)

        await db.execute(
            update(DownloadJob)
            .where(DownloadJob.id == job.id, DownloadJob.status == "processing")
            .values(
                status="pending",
                retry_count=retry_count,
                next_retry_at=next_retry_at,
                error=error_text,
                last_error=error_text,
                error_category=decision.category.value,
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()

        retry_download.apply_async(
            args=[str(job.id)],
            countdown=delay,
        )
    else:
        await _move_to_dlq(db, job, error, decision)


async def _move_to_dlq(
    db: AsyncSession,
    job: DownloadJob,
    error: Exception,
    decision: RetryDecision,
) -> None:
    """Move a permanently failed job to the dead-letter queue.

    Both the DownloadJob status update and FailedJob insertion happen in a
    single transaction so either both records persist or neither does.
    """
    failed = FailedJob(
        id=__import__("uuid").uuid4(),
        original_job_id=job.id,
        user_id=job.user_id,
        url=job.url,
        error_category=decision.category.value,
        final_error=decision.final_error or str(error),
        final_error_category=decision.category.value,
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
            error=decision.final_error or str(error),
            last_error=decision.final_error or str(error),
            error_category=decision.category.value,
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

    # Dispatch the DLQ handler to the dedicated `dlq` Celery queue (routed via
    # task_routes) for alerting/notification hooks. The DownloadJob/FailedJob
    # rows are already committed above, so a broker hiccup here must not
    # propagate and be mistaken for a failed DLQ commit.
    try:
        handle_failed_job.delay(str(job.id), str(error))
    except Exception:
        _sync_logger.warning(
            "dlq_handler_dispatch_failed",
            job_id=str(job.id),
            error=str(error),
            exc_info=True,
        )


async def _requeue_job(job_id: UUID, db: AsyncSession) -> None:
    """Requeue a job during shutdown or cancellation."""
    await db.execute(
        update(DownloadJob)
        .where(DownloadJob.id == job_id, DownloadJob.status == "processing")
        .values(status="pending", updated_at=datetime.now(UTC))
    )
    await db.commit()


async def _publish_progress(user_id: UUID, job_id: UUID, data: dict) -> None:
    """Publish progress update via pub/sub.

    Async so the executor can await it on the task's own event loop — no
    per-callback event loop is created, and the shared pub/sub Redis client
    stays on a single loop. Best-effort: a failing publish must never fail
    the download.
    """
    try:
        from app.services.pubsub_service import get_pubsub_service

        pubsub = get_pubsub_service()
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
    except Exception:
        logger.warning("progress_publish_failed", job_id=str(job_id), exc_info=True)


@shared_task(
    name="worker.celery_tasks.retry_download",
    queue="retries",
    acks_late=True,
)
def retry_download(job_id: str) -> dict:
    """Retry a previously failed download job.

    Scheduled by process_download when a retryable error occurs.
    """
    return cast(dict, process_download(job_id))


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

    async def _cleanup() -> dict:
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
                    if os.path.exists(safe_path):  # noqa: ASYNC240 — fast local fs ops
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

    async def _cleanup() -> dict:
        async with session_factory() as db:
            now = datetime.now(UTC)
            result = await db.execute(select(FailedJob).where(FailedJob.expires_at < now))
            expired = result.scalars().all()
            count = len(expired)
            for entry in expired:
                await db.delete(entry)
            await db.commit()

            # Reflect the actual remaining DLQ depth instead of zeroing the
            # gauge: only expired rows were removed, so non-expired entries
            # must stay visible in ytprocessor_dlq_depth.
            remaining = await db.execute(
                select(func.count()).select_from(FailedJob).where(FailedJob.expires_at >= now)
            )
            DLQ_DEPTH.set(remaining.scalar_one())
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

    async def _sweep() -> dict:
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

    async def _enqueue() -> dict:
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
