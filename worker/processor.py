"""Job processing with error classification, per-category retry, and circuit-aware deferral.

Each error is classified into a category with its own retry policy:
  RATE_LIMITED    5 retries  60s base  decorrelated jitter
  TRANSIENT       3 retries  10s base  decorrelated jitter
  BLOCKED/NOT_FOUND  0 retries — permanent failure
  TIMEOUT         2 retries  30s base  full jitter
  STORAGE         1 retry    5m fixed
  UNKNOWN         2 retries  30s base  full jitter

Circuit breaker open → defer (not fail) → auto-retry when circuit closes.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update

from app.services.circuit_breaker import (
    CircuitBreakerOpenError,
    get_youtube_circuit_breaker,
)
from app.services.error_classifier import (
    CATEGORY_POLICIES,
    ErrorCategory,
    calculate_delay,
    classify_error,
    extract_retry_after,
    format_attempt_error,
    is_non_retryable,
)
from app.services.pubsub_service import get_pubsub_service
from core.database import get_async_session_factory
from core.logging_config import get_logger
from core.metrics import (
    CIRCUIT_DEFERRED_DEPTH,
    DLQ_DEPTH,
    ERROR_CLASSIFICATION,
    JOB_DURATION_SECONDS,
    JOBS_COMPLETED,
    RECOVERIES,
    RETRIES_TOTAL,
)
from core.models.download_job import DownloadJob
from core.models.failed_job import FailedJob
from core.models.outbox import Outbox
from core.queue import push_to_download_queue, push_to_retry_queue, redis_client
from worker import job_claimer, job_executor
from worker.health import update_worker_state

logger = get_logger(__name__)

# -- Circuit deferred queue -------------------------------------------------

_CIRCUIT_DEFERRED_KEY = "circuit_deferred_queue"


async def _defer_job_to_circuit(job_id: UUID, service: str) -> None:
    try:
        ts = time.time()
        await redis_client.zadd(_CIRCUIT_DEFERRED_KEY, {str(job_id): ts})
        try:
            depth = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
            CIRCUIT_DEFERRED_DEPTH.set(depth)
        except Exception:
            pass
    except Exception as e:
        logger.error("failed_to_defer_job", job_id=str(job_id), error=str(e))


async def _drain_circuit_deferred(max_batch: int = 10) -> int:
    """Move deferred jobs back to download queue when circuit has recovered.

    Updates DB status 'deferred' → 'pending' atomically so the worker's
    claim (WHERE status='pending') succeeds on re-pickup.
    Returns number of jobs drained.
    """
    if not _circuit_is_accepting():
        return 0
    try:
        members = await redis_client.zrange(_CIRCUIT_DEFERRED_KEY, 0, max_batch - 1)
        if not members:
            return 0
        await redis_client.zrem(_CIRCUIT_DEFERRED_KEY, *members)

        session_factory = get_async_session_factory()
        drained = 0
        async with session_factory() as db:
            for job_id_str in members:
                result = await db.execute(
                    update(DownloadJob)
                    .where(DownloadJob.id == job_id_str, DownloadJob.status == "deferred")
                    .values(status="pending", updated_at=datetime.now(UTC))
                )
                if result.rowcount == 1:
                    await push_to_download_queue(job_id_str)
                    drained += 1
                else:
                    logger.warning(
                        "circuit_drain_db_mismatch",
                        job_id=job_id_str,
                        reason="job not in deferred state or not found",
                    )
            await db.commit()

        try:
            remaining = await redis_client.zcard(_CIRCUIT_DEFERRED_KEY)
            CIRCUIT_DEFERRED_DEPTH.set(remaining)
        except Exception:
            pass
        return drained
    except Exception as e:
        logger.error("circuit_drain_failed", error=str(e))
        return 0


def _circuit_is_accepting() -> bool:
    cb = get_youtube_circuit_breaker()
    return not cb.is_open


# -- Dead letter queue ------------------------------------------------------


async def _move_to_dlq(
    db,
    job: DownloadJob,
    category: ErrorCategory,
    final_error: str,
    retry_count: int,
    retry_history: str | None = None,
) -> None:
    failed = FailedJob(
        id=uuid.uuid4(),
        original_job_id=job.id,
        user_id=job.user_id,
        url=job.url,
        error_category=category.value,
        retry_history=retry_history if retry_history is not None else job.error,
        final_error=final_error,
        final_error_category=category.value,
        retry_count=retry_count,
        max_retries_at_failure=job.max_retries,
        title=job.title,
        failed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(failed)
    try:
        from sqlalchemy import func as sa_func

        result = await db.execute(sa_func.count(FailedJob.id))
        DLQ_DEPTH.set(result.scalar())
    except Exception:
        pass


async def process_next_job(job_id: UUID | str | None = None) -> bool:
    """Claim and process one worker job through the extracted execution boundary."""
    resolved_job_id = await job_claimer.next_job_id(job_id)
    if resolved_job_id is None:
        return False

    session_factory = get_async_session_factory()
    start_time = time.time()

    async with session_factory() as db:
        job = await job_claimer.claim_next(db, resolved_job_id)
        if job is None:
            logger.info("job_not_claimed", job_id=str(resolved_job_id))
            return False

        active_job_id = job.id
        update_worker_state(status="running", current_job_started_at=datetime.now(UTC).isoformat())

        try:
            await job_executor.publish_job_status(job)
            execution_result = await job_executor.execute(db, job, start_time=start_time)
            return await _handle_execution_result(db, job, execution_result)

        except asyncio.CancelledError:
            logger.info("Job %s cancelled, requeueing...", active_job_id)
            await job_executor.requeue_job(active_job_id, db)
            update_worker_state(status="running", current_job_started_at=None)
            raise

        finally:
            JOB_DURATION_SECONDS.observe(time.time() - start_time)

    return False


async def _handle_execution_result(
    db,
    job: DownloadJob,
    result: job_executor.ExecutionResult,
) -> bool:
    if result.status == job_executor.ExecutionStatus.COMPLETED:
        return True
    if result.status in {
        job_executor.ExecutionStatus.CONSUMED,
        job_executor.ExecutionStatus.REQUEUED,
    }:
        return result.completed
    if result.error is None:
        return False
    if isinstance(result.error, CircuitBreakerOpenError):
        return await _handle_circuit_open(db, job.id, result.error)
    return await _handle_execution_error(db, job, result.error)


async def _handle_circuit_open(db, active_job_id: UUID, cb_error: CircuitBreakerOpenError) -> bool:
    logger.warning(
        "circuit_breaker_open_deferring",
        job_id=str(active_job_id),
        service=cb_error.service_name,
        reset_timeout=cb_error.reset_timeout,
    )
    result = await db.execute(
        update(DownloadJob)
        .where(
            DownloadJob.id == active_job_id,
            DownloadJob.status == "processing",
        )
        .values(
            status="deferred",
            error=f"Circuit breaker open ({cb_error.service_name}), "
            f"deferred until recovery (cooldown: {cb_error.reset_timeout}s)",
            error_category="transient",
            updated_at=datetime.now(UTC),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        logger.warning(
            "circuit_deferral_skipped_job_not_processing",
            job_id=str(active_job_id),
        )
        return False

    await _defer_job_to_circuit(active_job_id, cb_error.service_name)

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    deferred_job = select_result.scalar_one_or_none()
    if deferred_job:
        await job_executor.publish_job_status(deferred_job)

    JOBS_COMPLETED.labels(status="deferred").inc()
    update_worker_state(status="running", current_job_started_at=None)
    return False


async def _handle_execution_error(db, claimed_job: DownloadJob, error: BaseException) -> bool:
    active_job_id = claimed_job.id
    error_str = str(error)
    classification = classify_error(error_str)
    category = classification.category
    job_max_retries = claimed_job.max_retries if claimed_job.max_retries else 3

    cat_policy = CATEGORY_POLICIES[category]
    effective_max = min(cat_policy.max_retries, job_max_retries)

    ERROR_CLASSIFICATION.labels(category=category.value).inc()

    update_worker_state(status="running", current_job_started_at=None)

    select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
    job = select_result.scalar_one_or_none()

    if not job:
        logger.error("job_not_found_during_error_handling", job_id=str(active_job_id))
        return False

    if is_non_retryable(category) or job.retry_count >= effective_max:
        if is_non_retryable(category):
            logger.info(
                "job_failed_non_retryable",
                job_id=str(active_job_id),
                category=category.value,
                signal=classification.signal,
            )
            final_error = f"Non-retryable error ({category.value}): {error_str}"
        else:
            logger.warning(
                "job_failed_permanently_max_retries",
                job_id=str(active_job_id),
                category=category.value,
                retry_count=job.retry_count,
                effective_max=effective_max,
            )
            final_error = (
                f"Max retries ({effective_max}) exceeded for "
                f"'{category.value}' category: {error_str}"
            )

        previous_errors = job.error or ""
        if previous_errors:
            accumulated = f"{previous_errors} → {final_error}"
        else:
            accumulated = final_error

        failed_result = await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.id == active_job_id,
                DownloadJob.status == "processing",
            )
            .values(
                status="failed",
                error=accumulated,
                error_category=category.value,
                completed_at=datetime.now(UTC),
            )
        )
        if failed_result.rowcount == 0:
            logger.warning(
                "job_failed_update_skipped_not_processing",
                job_id=str(active_job_id),
            )
            JOBS_COMPLETED.labels(status="failed").inc()
            await db.commit()
            return False

        JOBS_COMPLETED.labels(status="failed").inc()
        await db.commit()

        await _move_to_dlq(
            db,
            job,
            category,
            final_error,
            job.retry_count,
            retry_history=accumulated,
        )
        await db.commit()

        select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
        failed_job = select_result.scalar_one_or_none()
        if failed_job:
            await job_executor.publish_job_status(failed_job)

        logger.info("job_moved_to_dlq", job_id=str(active_job_id), category=category.value)
        return False

    prev_delay = None
    if job.next_retry_at:
        prev_delay = (job.next_retry_at - datetime.now(UTC)).total_seconds()
        if prev_delay < 0:
            prev_delay = None

    delay_seconds = calculate_delay(
        category=category,
        attempt=job.retry_count,
        prev_delay=prev_delay,
        retry_after=extract_retry_after(error_str),
    )
    next_retry = datetime.now(UTC) + timedelta(seconds=delay_seconds)

    formatted_error = format_attempt_error(
        attempt=job.retry_count + 1,
        max_retries=effective_max,
        error_str=error_str,
        category=category,
    )

    previous = job.error or ""
    if previous and job.retry_count > 0:
        accumulated_error = f"{previous} → {formatted_error}"
    else:
        accumulated_error = formatted_error

    outbox_entry = Outbox(
        id=uuid.uuid4(),
        job_id=active_job_id,
        event_type="retry_scheduled",
        payload=json.dumps(
            {
                "retry_count": job.retry_count + 1,
                "category": category.value,
                "next_retry_at": next_retry.isoformat(),
            }
        ),
        status="pending",
    )
    db.add(outbox_entry)

    retry_result = await db.execute(
        update(DownloadJob)
        .where(
            DownloadJob.id == active_job_id,
            DownloadJob.status == "processing",
        )
        .values(
            status="pending",
            retry_count=job.retry_count + 1,
            next_retry_at=next_retry,
            error=accumulated_error,
            error_category=category.value,
            updated_at=datetime.now(UTC),
        )
    )
    await db.commit()
    if retry_result.rowcount == 0:
        logger.warning(
            "job_retry_update_skipped_not_processing",
            job_id=str(active_job_id),
        )
        return False

    try:
        retry_ts = next_retry.timestamp()
        await push_to_retry_queue(active_job_id, retry_ts)
        await db.execute(delete(Outbox).where(Outbox.id == outbox_entry.id))
        await db.commit()

        RETRIES_TOTAL.labels(category=category.value).inc()

        logger.info(
            "job_scheduled_for_retry",
            job_id=str(active_job_id),
            category=category.value,
            retry_count=job.retry_count + 1,
            effective_max=effective_max,
            next_retry_at=next_retry.isoformat(),
            delay_seconds=round(delay_seconds, 1),
        )

        select_result = await db.execute(select(DownloadJob).where(DownloadJob.id == active_job_id))
        retried_job = select_result.scalar_one_or_none()
        if retried_job:
            await job_executor.publish_job_status(retried_job)

    except Exception as enqueue_error:
        logger.error(
            "job_failed_to_enqueue_for_retry",
            job_id=str(active_job_id),
            error=str(enqueue_error),
        )

    return False


async def reset_stuck_jobs(timeout_minutes: int = 10) -> int:
    """Reset jobs stuck in 'processing' status to 'failed'.

    Separate from the zombie sweeper (which requeues as pending).
    This is the hard timeout that marks jobs as failed.
    """
    # Note: this function is called from worker.main's periodic cleanup, not
    # from the zombie sweeper. The zombie sweeper (requeue_stuck_jobs) resets
    # to 'pending'; this function marks them as permanently 'failed'.
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    session_factory = get_async_session_factory()

    async with session_factory() as db:
        # Single UPDATE with RETURNING — avoids N+1 SELECT-per-job pattern
        # that was previously present via select() + per-job db.refresh().
        result = await db.execute(
            update(DownloadJob)
            .where(
                DownloadJob.status == "processing",
                DownloadJob.updated_at < cutoff,
            )
            .values(
                status="failed",
                error="Job timed out",
                error_category="timeout",
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            .returning(DownloadJob.id, DownloadJob.user_id)
            .execution_options(synchronize_session=False)
        )
        affected = result.fetchall()
        if not affected:
            return 0

        await db.commit()

        # Batch-publish status updates without per-job SELECT queries
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


async def sync_outbox_to_queue(batch_size: int = 100) -> int:
    """Sync pending outbox entries to Redis queue.

    Pending rows are selected with ``FOR UPDATE SKIP LOCKED`` where supported,
    pushed to Redis, and deleted only after Redis confirms the enqueue. Failed
    pushes remain ``pending`` for the next sync cycle.

    This is intentionally at-least-once. Queue helpers deduplicate Redis entries,
    so a crash after Redis push but before DB delete can be retried without
    creating unbounded duplicate work, while a Redis outage cannot lose outbox
    rows by moving them to a terminal "enqueued" state too early.
    """
    session_factory = get_async_session_factory()
    synced = 0

    async with session_factory() as db:
        claim_result = await db.execute(
            select(Outbox)
            .where(Outbox.status == "pending")
            .order_by(Outbox.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        entries = claim_result.scalars().all()

        if not entries:
            return 0

        processed_entry_ids = []
        for entry in entries:
            try:
                enqueued = False
                if entry.event_type == "retry_scheduled":
                    payload_data = json.loads(entry.payload) if entry.payload else {}
                    next_retry_at = payload_data.get("next_retry_at")
                    if next_retry_at:
                        retry_timestamp = datetime.fromisoformat(next_retry_at).timestamp()
                        enqueued = await push_to_retry_queue(entry.job_id, retry_timestamp)
                    else:
                        logger.error("missing_next_retry_at_in_payload", job_id=str(entry.job_id))
                        continue
                else:
                    enqueued = await push_to_download_queue(entry.job_id)
                if enqueued:
                    processed_entry_ids.append(entry.id)
                    synced += 1
            except Exception as e:
                logger.error(
                    "failed_to_enqueue_job_from_outbox", job_id=str(entry.job_id), error=str(e)
                )

        if processed_entry_ids:
            try:
                await db.execute(delete(Outbox).where(Outbox.id.in_(processed_entry_ids)))
                await db.commit()
            except Exception:
                await db.rollback()

    if synced > 0:
        logger.info("synced_outbox_entries_to_queue", count=synced)

    return synced


async def cleanup_stale_outbox_entries(hours: int = 24) -> int:
    """Delete outbox entries that are older than the given threshold.

    This provides periodic cleanup for entries that escaped normal
    processing (e.g., stuck 'enqueued' rows, orphaned entries).

    Only deletes entries with status 'enqueued' or 'completed' — pending
    entries are kept for crash recovery (sync_outbox_to_queue needs them).
    """
    session_factory = get_async_session_factory()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with session_factory() as db:
        result = await db.execute(
            delete(Outbox).where(
                Outbox.created_at < cutoff, Outbox.status.in_(["enqueued", "completed"])
            )
        )
        await db.commit()
        count = int(result.rowcount or 0)
        if count > 0:
            logger.info("outbox_cleanup_completed", deleted=count, cutoff_age_hours=hours)
        return count
